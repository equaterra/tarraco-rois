from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
import torch
import yaml
from affine import Affine
from PIL import Image
from shapely.geometry import MultiPolygon, box, mapping, shape
from shapely.ops import unary_union
from transformers import Owlv2ForObjectDetection, Owlv2Processor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
MODULE_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

config = yaml.safe_load(open(CONFIG_PATH))
module_config = yaml.safe_load(open(MODULE_CONFIG_PATH)) if MODULE_CONFIG_PATH.exists() else {}
config.update(module_config)

RAW_DIR = PROJECT_ROOT / config.get("output_path", "dist/masks") / "owlv2"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Zero-shot object detection with OWL-ViT v2
# ---------------------------------------------------------------------------

_device = None

def _get_device():
    global _device
    if _device is None:
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        log.info("OWLv2 using device: %s", _device)
    return _device

_processor = None
_model = None

def get_model():
    global _processor, _model
    if _model is None:
        dev = _get_device()
        name = module_config.get("model_name", "google/owlv2-base-patch16-ensemble")
        _processor = Owlv2Processor.from_pretrained(name)
        _model = Owlv2ForObjectDetection.from_pretrained(name).to(dev).eval()
    return _processor, _model

def create_patches(image: np.ndarray, tile_size: int, overlap: int):
    h, w = image.shape[:2]
    stride = tile_size - overlap
    patches = []
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            y1, x1 = y, x
            y2 = min(y + tile_size, h)
            x2 = min(x + tile_size, w)
            if y2 - y1 < 32 or x2 - x1 < 32:
                continue
            patches.append(((x1, y1, x2, y2), image[y1:y2, x1:x2]))
    return patches

def nms_detections(boxes, scores, iou_threshold=0.3):
    if len(boxes) == 0:
        return []
    boxes = np.array(boxes)
    scores = np.array(scores)
    order = np.argsort(scores)[::-1]
    keep = []
    while len(order) > 0:
        i = order[0]
        keep.append(i)
        if len(order) == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        inter = w * h
        union = area_i + area_o - inter
        iou = inter / np.maximum(union, 1e-6)
        order = order[1:][iou <= iou_threshold]
    return keep

def run_owlv2(
    tif_path: Path,
    output_dir: Optional[Path] = None,
    building_id: str = "building",
    log_metadata: dict | None = None,
) -> dict:
    t0 = time.time()
    output_dir = output_dir or RAW_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    prompts = module_config.get("prompts", ["solar panel", "photovoltaic panel"])
    score_threshold = module_config.get("score_threshold", 0.15)
    tile_size = module_config.get("subtile_size_px", 288)
    overlap = module_config.get("subtile_overlap_px", 48)
    nms_iou = module_config.get("nms_iou_threshold", 0.3)
    min_area = module_config.get("min_polygon_area_m2", 0.5)
    max_area = module_config.get("max_polygon_area_m2", 10.0)
    max_occ = module_config.get("max_tile_occupancy", 0.05)
    max_aspect = module_config.get("max_aspect_ratio", 4.0)
    merge_dist = module_config.get("merge_distance_m", 1.0)

    log.info("OWLv2: loading model ...")
    processor, model = get_model()
    dev = _get_device()

    log.info("OWLv2: reading %s", tif_path)
    with rasterio.open(tif_path) as src:
        img_np = src.read([1, 2, 3]).transpose(1, 2, 0)
        transform = src.transform
        crs = src.crs

    h, w = img_np.shape[:2]
    tile_w_m = w * abs(transform.a)
    tile_h_m = h * abs(transform.e)
    tile_area_m2 = tile_w_m * tile_h_m
    tile_occ_thresh = tile_area_m2 * max_occ

    patches = create_patches(img_np, tile_size, overlap)
    log.info("OWLv2: %d sub-tiles to process", len(patches))

    pixel_boxes = []
    pixel_scores = []
    patch_origins = []

    for (x1, y1, x2, y2), patch in patches:
        patch_h, patch_w = patch.shape[:2]
        if patch_h < 64 or patch_w < 64:
            continue
        pil_img = Image.fromarray(patch)

        for prompt in prompts:
            try:
                inputs = processor(
                    text=[prompt],
                    images=pil_img,
                    return_tensors="pt",
                ).to(dev)

                with torch.no_grad():
                    outputs = model(**inputs)

                target_sizes = torch.tensor([[patch_h, patch_w]], device=dev)
                results = processor.post_process_object_detection(
                    outputs, threshold=score_threshold, target_sizes=target_sizes
                )[0]

                for box, score, label in zip(results["boxes"], results["scores"], results["labels"]):
                    bx1, by1, bx2, by2 = box.tolist()
                    pixel_boxes.append([bx1 + x1, by1 + y1, bx2 + x1, by2 + y1])
                    pixel_scores.append(float(score))
                    patch_origins.append((x1, y1))
            except Exception as e:
                log.warning("OWLv2 prompt '%s' failed on patch (%d,%d): %s", prompt, x1, y1, e)

    raw_count = len(pixel_boxes)
    log.info("OWLv2: %d raw detections before NMS", raw_count)

    if raw_count == 0:
        meta = {"model": "owlv2", "building_id": building_id, "detections": 0, "raw_detections": 0, "time_s": time.time() - t0}
        _save_results(output_dir, building_id, [], meta)
        return meta

    keep = nms_detections(pixel_boxes, pixel_scores, nms_iou)
    pixel_boxes = [pixel_boxes[i] for i in keep]
    pixel_scores = [pixel_scores[i] for i in keep]
    log.info("OWLv2: %d after NMS", len(pixel_boxes))

    polygons = []
    for (bx1, by1, bx2, by2) in pixel_boxes:
        ul = transform * (bx1, by1)
        ur = transform * (bx2, by1)
        lr = transform * (bx2, by2)
        ll = transform * (bx1, by2)
        poly = box(ul[0], ll[1], ur[0], lr[1])
        polygons.append(poly)

    # geometric filters
    filtered = []
    for poly in polygons:
        area = poly.area
        if area < min_area or area > max_area:
            continue
        if area > tile_occ_thresh:
            continue
        minx, miny, maxx, maxy = poly.bounds
        w_ = maxx - minx
        h_ = maxy - miny
        if w_ == 0 or h_ == 0:
            continue
        ar = max(w_, h_) / min(w_, h_)
        if ar > max_aspect:
            continue
        filtered.append(poly)

    log.info("OWLv2: %d after geometric filters", len(filtered))

    # merge nearby
    if filtered and merge_dist > 0:
        merged = unary_union([p.buffer(merge_dist / 2) for p in filtered])
        if merged.geom_type == "MultiPolygon":
            merged = MultiPolygon([p for p in merged.geoms if p.area >= min_area])
        elif merged.geom_type == "Polygon":
            merged = MultiPolygon([merged]) if merged.area >= min_area else MultiPolygon()
        else:
            merged = MultiPolygon()
        filtered = list(merged.geoms)

    meta = {
        "model": "owlv2",
        "building_id": building_id,
        "detections": len(filtered),
        "raw_detections": raw_count,
        "after_nms": len(pixel_boxes),
        "score_threshold": score_threshold,
        "nms_iou": nms_iou,
        "subtile_size_px": tile_size,
        "subtile_overlap_px": overlap,
        "time_s": round(time.time() - t0, 2),
    }

    _save_results(output_dir, building_id, filtered, meta)
    return meta


def _save_results(output_dir: Path, building_id: str, polygons: list, meta: dict):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {"id": f"{building_id}_{i:04d}"},
            }
            for i, poly in enumerate(polygons)
        ],
    }
    geojson_path = output_dir / f"{building_id}.geojson"
    with open(geojson_path, "w") as f:
        json.dump(geojson, f, indent=2)
    log.info("OWLv2: wrote %s (%d features)", geojson_path, len(polygons))

    yaml_path = output_dir / f"{building_id}_meta.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(meta, f)
    log.info("OWLv2: wrote %s", yaml_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m modules.owlv2.owlv2 <tif_path> [building_id]")
        sys.exit(1)
    tif_path = Path(sys.argv[1])
    building_id = sys.argv[2] if len(sys.argv) > 2 else tif_path.stem
    run_owlv2(tif_path, building_id=building_id)
