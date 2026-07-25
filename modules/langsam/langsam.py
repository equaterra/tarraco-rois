#!/usr/bin/env python3
"""
langsam.py — Automated PV segmentation using Language Segment-Anything

Workflow
--------
  1. Read a large ortho mosaic (GeoTIFF / GPKG) from sandbox/data/ortho/.
  2. Load a grid of UTM 1km quadricules (GeoJSON / GPKG) from res/data/shp/.
  3. For each quadricule that spatially intersects the ortho:
       a. Clip the ortho to the quadricule bbox  -> core tile
       b. Tile the clip into 800x800 sub-tiles with 128px overlap
       c. Run LangSAM with each PV text prompt per sub-tile
       d. Merge overlapping detections across sub-tiles (NMS + dedup)
       e. Apply geometric filters (area, aspect ratio, occupancy)
       f. Convert pixel masks -> georeferenced polygons (EPSG:25831)
       g. Write  dist/masks/langsam/<TILE_ID>_core.geojson
                 dist/masks/langsam/<TILE_ID>_donut.geojson  (if --process-donut)
  4. Print a summary table (tile, detections, skipped).

RESUME SUPPORT
--------------
Before processing a tile, the script checks whether the expected output
file(s) already exist in dist/masks/langsam/. If so, the tile is skipped
(status "ALREADY DONE") unless --overwrite is passed.

Usage
-----
  python -m modules.langsam.langsam --ortho sandbox/data/ortho/ortofoto.tif --grid res/data/tiles/test_grid.geojson
  python -m modules.langsam.langsam --ortho ... --tile-id 31TCF4157 --overwrite

Dependencies
------------
  torch torchvision (install separately with CUDA support)
  lang-sam (pip install git+https://github.com/luca-medeiros/lang-segment-anything.git)
  rasterio, geopandas, shapely, numpy, Pillow, click, pyyaml, tqdm, affine
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import click
import geopandas as gpd
import numpy as np
import rasterio
import yaml
from affine import Affine
from PIL import Image
from rasterio.features import shapes as rio_shapes
from rasterio.mask import mask as rio_mask
from shapely.geometry import MultiPolygon, box, mapping, shape
from shapely.ops import unary_union
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
MODULE_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

config = yaml.safe_load(open(CONFIG_PATH))
module_config = yaml.safe_load(open(MODULE_CONFIG_PATH)) if MODULE_CONFIG_PATH.exists() else {}
config.update(module_config)

RAW_DIR = PROJECT_ROOT / config.get("output_path", "dist/masks") / "langsam"
RAW_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_GRID = PROJECT_ROOT / "res" / "data" / "tiles" / "test_grid.geojson"
DEFAULT_ORTHO = PROJECT_ROOT / config.get("ortho_path", "sandbox/data/ortho")

# Geometric filters
MIN_POLYGON_AREA_M2 = float(config.get("min_polygon_area_m2", 1.0))
MAX_POLYGON_AREA_M2 = float(config.get("max_polygon_area_m2", 50000.0))
MERGE_DISTANCE_M = float(config.get("merge_distance_m", 5.0))
MAX_TILE_OCCUPANCY = float(config.get("max_tile_occupancy", 0.25))
MAX_ASPECT_RATIO = float(config.get("max_aspect_ratio", 8.0))

# Sub-tile configuration for GroundingDINO input
SUBTILE_SIZE_PX = int(config.get("subtile_size_px", 800))
SUBTILE_OVERLAP_PX = int(config.get("subtile_overlap_px", 128))

# NMS IoU threshold
NMS_IOU_THRESHOLD = float(config.get("nms_iou_threshold", 0.5))

# ---------------------------------------------------------------------------
# PV text prompts for GroundingDINO
# Each phrase must end with "." — GroundingDINO convention.
# Run one prompt at a time to avoid unwanted cross-associations.
# ---------------------------------------------------------------------------
PV_PROMPTS: list[str] = [
    "solar panel.",
    "photovoltaic panel.",
    "ground-mounted solar panel array.",
    "photovoltaic solar farm.",
    "rooftop solar panels.",
]


# ---------------------------------------------------------------------------
# Model version tracking
# ---------------------------------------------------------------------------

def get_model_versions() -> dict:
    """Collect version info for GroundingDINO, SAM2, and lang-sam."""
    versions = {}

    # lang-sam
    try:
        import lang_sam
        versions["lang_sam"] = getattr(lang_sam, "__version__", "unknown")
    except Exception:
        versions["lang_sam"] = "unknown"

    # SAM2
    try:
        import sam2
        versions["sam2"] = getattr(sam2, "__version__", "unknown")
    except Exception:
        versions["sam2"] = "unknown"

    # GroundingDINO
    try:
        import groundingdino
        versions["grounding_dino"] = getattr(groundingdino, "__version__", "unknown")
    except Exception:
        try:
            from groundingdino import __version__ as gd_version
            versions["grounding_dino"] = gd_version
        except Exception:
            versions["grounding_dino"] = "unknown"

    # Torch
    try:
        import torch
        versions["torch"] = torch.__version__
        versions["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            versions["cuda_device"] = torch.cuda.get_device_name(0)
    except Exception:
        versions["torch"] = "unknown"

    # Transformers
    try:
        import transformers
        versions["transformers"] = transformers.__version__
    except Exception:
        versions["transformers"] = "unknown"

    # PIL / Pillow
    try:
        import PIL
        versions["pillow"] = PIL.__version__
    except Exception:
        versions["pillow"] = "unknown"

    return versions


# ---------------------------------------------------------------------------
# Raster helpers
# ---------------------------------------------------------------------------

def clip_ortho_to_geom(
    ortho_path: Path, geom, geom_crs_epsg: int, min_data_fraction: float = 0.05
) -> Optional[Path]:
    """
    Clip ortho_path to geom (in geom_crs_epsg). Returns path to temp GeoTIFF,
    or None if the geometry does not intersect the raster extent, OR if the
    clipped area is almost entirely nodata.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    with rasterio.open(ortho_path) as src:
        gdf = gpd.GeoDataFrame(geometry=[geom], crs=f"EPSG:{geom_crs_epsg}")
        gdf = gdf.to_crs(src.crs)

        raster_box = box(*src.bounds)
        if not gdf.iloc[0].geometry.intersects(raster_box):
            tmp_path.unlink(missing_ok=True)
            return None

        out_image, out_transform = rio_mask(src, gdf.geometry.values, crop=True)
        meta = src.meta.copy()
        meta.update(
            driver="GTiff",
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
        )

        if out_image.shape[0] >= 4:
            alpha = out_image[3]
            data_fraction = np.count_nonzero(alpha) / alpha.size
            if data_fraction < min_data_fraction:
                tmp_path.unlink(missing_ok=True)
                return None

    with rasterio.open(tmp_path, "w", **meta) as dst:
        dst.write(out_image)

    return tmp_path


def tif_to_pil(tif_path: Path) -> tuple[Image.Image, Affine, str]:
    """
    Read a GeoTIFF and return (PIL RGB image, affine transform, CRS WKT).
    Handles 1-band (grayscale -> RGB) and n-band rasters.
    """
    with rasterio.open(tif_path) as src:
        n = src.count
        if n >= 3:
            data = src.read([1, 2, 3])
        else:
            band = src.read(1)
            data = np.stack([band, band, band])
        transform = src.transform
        crs_wkt = src.crs.to_wkt() if src.crs else ""

    rgb = np.moveaxis(data, 0, -1)
    if rgb.dtype != np.uint8:
        dmax = rgb.max()
        rgb = ((rgb / dmax) * 255).astype(np.uint8) if dmax > 0 else rgb.astype(np.uint8)

    return Image.fromarray(rgb), transform, crs_wkt


# ---------------------------------------------------------------------------
# Sub-tiling: split image into 800x800 patches for GroundingDINO
# ---------------------------------------------------------------------------

def create_patches(
    image: Image.Image,
    patch_size: int = SUBTILE_SIZE_PX,
    overlap: int = SUBTILE_OVERLAP_PX,
) -> list[dict]:
    """
    Split a PIL image into overlapping patches.

    Returns list of dicts:
        {"patch": PIL.Image, "offset_x": int, "offset_y": int,
         "width": int, "height": int}
    """
    w, h = image.size
    stride = patch_size - overlap

    patches = []
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            x2 = min(x + patch_size, w)
            y2 = min(y + patch_size, h)
            x1 = max(0, x2 - patch_size)
            y1 = max(0, y2 - patch_size)

            patch = image.crop((x1, y1, x2, y2))
            patches.append({
                "patch": patch,
                "offset_x": x1,
                "offset_y": y1,
                "width": x2 - x1,
                "height": y2 - y1,
            })

    return patches


def merge_patch_results(
    patch_results: list[dict],
    patch_size: int = SUBTILE_SIZE_PX,
) -> list[dict]:
    """
    Merge detection results from overlapping patches into full-image
    coordinates, applying NMS to deduplicate.

    Each item in patch_results is a dict:
        {"polygon": shapely Polygon, "score": float,
         "offset_x": int, "offset_y": int, "prompt": str}
    """
    if not patch_results:
        return []

    # Convert patch-local polygons to full-image coordinates
    for r in patch_results:
        r["polygon"] = r["polygon"].translate(r["offset_x"], r["offset_y"])

    # Sort by score descending for NMS
    patch_results.sort(key=lambda r: r["score"], reverse=True)

    kept = []
    suppressed = set()

    for i, ri in enumerate(patch_results):
        if i in suppressed:
            continue

        # Check overlap with already-kept detections
        is_duplicate = False
        for j, rj in enumerate(kept):
            if ri["polygon"].intersects(rj["polygon"]):
                inter_area = ri["polygon"].intersection(rj["polygon"]).area
                union_area = ri["polygon"].union(rj["polygon"]).area
                if union_area > 0 and (inter_area / union_area) > NMS_IOU_THRESHOLD:
                    is_duplicate = True
                    break

        if not is_duplicate:
            kept.append(ri)

    return kept


# ---------------------------------------------------------------------------
# Pixel masks -> polygons
# ---------------------------------------------------------------------------

def pixel_masks_to_polygons(masks: np.ndarray, transform: Affine) -> list:
    """
    Union N binary masks (N x H x W) and vectorise pixel blobs to
    georeferenced Shapely polygons using the raster affine transform.
    """
    combined = np.zeros(masks.shape[-2:], dtype=np.uint8)
    for m in masks:
        combined = np.logical_or(combined, m.astype(bool)).astype(np.uint8)

    return [
        shape(geom)
        for geom, val in rio_shapes(combined, transform=transform)
        if val == 1
    ]


# ---------------------------------------------------------------------------
# Geometric filters
# ---------------------------------------------------------------------------

def filter_polygons(polygons: list, min_area: float, max_area: float) -> list:
    """Remove polygons outside the area bounds."""
    return [p for p in polygons if min_area <= p.area <= max_area]


def filter_aspect_ratio(polygons: list, max_ratio: float) -> list:
    """Remove polygons with extreme aspect ratios (e.g. very long thin shapes)."""
    filtered = []
    for p in polygons:
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            continue
        bounds = p.bounds  # (minx, miny, maxx, maxy)
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        if w == 0 or h == 0:
            continue
        ratio = max(w, h) / min(w, h)
        if ratio <= max_ratio:
            filtered.append(p)
    return filtered


def filter_tile_occupancy(polygons: list, tile_area_px: int, resolution_m_per_px: float,
                           max_occupancy: float) -> list:
    """Remove polygons that occupy more than max_occupancy fraction of the tile."""
    tile_area_m2 = tile_area_px * (resolution_m_per_px ** 2)
    max_area_m2 = tile_area_m2 * max_occupancy
    return [p for p in polygons if p.area <= max_area_m2]


def nms_polygons(polygons: list, scores: list, iou_threshold: float = NMS_IOU_THRESHOLD) -> tuple[list, list]:
    """Non-maximum suppression on polygons. Returns (kept_polygons, kept_scores)."""
    if not polygons:
        return [], []

    indexed = sorted(range(len(polygons)), key=lambda i: scores[i], reverse=True)
    keep_idx = []
    suppressed = set()

    for i in indexed:
        if i in suppressed:
            continue
        keep_idx.append(i)
        for j in indexed:
            if j <= i or j in suppressed:
                continue
            inter = polygons[i].intersection(polygons[j])
            union = polygons[i].union(polygons[j])
            if union.area > 0 and (inter.area / union.area) > iou_threshold:
                suppressed.add(j)

    return [polygons[i] for i in keep_idx], [scores[i] for i in keep_idx]


def merge_nearby_polygons(polygons: list, distance: float) -> list:
    """Buffer and dissolve polygons that are within `distance` meters."""
    if not polygons or distance <= 0:
        return polygons
    buffered = [p.buffer(distance) for p in polygons]
    merged = unary_union(buffered)
    dissolved = [shape(g) for g in merged.geoms] if isinstance(merged, MultiPolygon) else [merged]
    return dissolved


# ---------------------------------------------------------------------------
# Donut geometry
# ---------------------------------------------------------------------------

def build_donut(tile_geom, overlap_fraction: float = 0.25):
    """Return the overlap ring (donut) around a tile."""
    buffer_m = 1000 * overlap_fraction
    outer = tile_geom.buffer(buffer_m, join_style=2)
    return outer.difference(tile_geom)


# ---------------------------------------------------------------------------
# LangSAM inference
# ---------------------------------------------------------------------------

_MODEL_CACHE = {}


def get_model() -> "LangSAM":  # noqa: F821
    """Load LangSAM once and reuse across tiles."""
    if "model" not in _MODEL_CACHE:
        log.info("Loading LangSAM model...")
        from lang_sam import LangSAM
        _MODEL_CACHE["model"] = LangSAM()
        log.info("LangSAM model loaded.")
    return _MODEL_CACHE["model"]


def clear_gpu_cache():
    """Release GPU memory between tiles."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except ImportError:
        pass


def run_langsam_on_image(
    pil_image: Image.Image,
    transform: Affine,
    box_threshold: float,
    text_threshold: float,
) -> list[dict]:
    """
    Run LangSAM on a single image (may be a sub-tile).

    Returns list of dicts:
        {"polygon": Shapely Polygon, "score": float, "prompt": str}
    """
    model = get_model()
    all_results: list = []

    for prompt in PV_PROMPTS:
        results = model.predict(
            [pil_image], [prompt],
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        for result in results:
            masks = result.get("masks")
            scores = result.get("scores")
            if masks is None or len(masks) == 0:
                continue
            masks_np = np.asarray(masks, dtype=bool)
            polys = pixel_masks_to_polygons(masks_np, transform)
            for k, poly in enumerate(polys):
                score = scores[k] if scores is not None and k < len(scores) else 1.0
                all_results.append({
                    "polygon": poly,
                    "score": float(score),
                    "prompt": prompt,
                })

    return all_results


def run_langsam(
    pil_image: Image.Image,
    transform: Affine,
    box_threshold: float = 0.40,
    text_threshold: float = 0.30,
    use_tiling: bool = True,
    subtile_size: int = SUBTILE_SIZE_PX,
    subtile_overlap: int = SUBTILE_OVERLAP_PX,
) -> list[dict]:
    """
    Run LangSAM with sub-tiling and NMS.

    If use_tiling is True and the image is larger than subtile_size,
    splits into overlapping patches, runs inference on each, and merges
    with NMS. Otherwise runs on the full image.

    Returns list of dicts:
        {"polygon": Shapely Polygon, "score": float, "prompt": str}
    """
    img_w, img_h = pil_image.size

    if not use_tiling or (img_w <= subtile_size and img_h <= subtile_size):
        return run_langsam_on_image(pil_image, transform, box_threshold, text_threshold)

    # Split into sub-tiles
    patches = create_patches(pil_image, subtile_size, subtile_overlap)
    log.info(f"Image {img_w}x{img_h} split into {len(patches)} sub-tiles ({subtile_size}px, {subtile_overlap}px overlap)")

    all_patch_results = []
    for patch_info in patches:
        patch_img = patch_info["patch"]
        px, py = patch_info["offset_x"], patch_info["offset_y"]

        # Compute transform for this patch's pixel space
        # Each patch is in its own pixel coords; we track offset for merging
        patch_transform = transform * Affine.translation(px, py)

        results = run_langsam_on_image(patch_img, patch_transform, box_threshold, text_threshold)
        for r in results:
            r["offset_x"] = 0  # already in geo coords via patch_transform
            r["offset_y"] = 0

        all_patch_results.extend(results)

    if not all_patch_results:
        return []

    # NMS dedup across patches
    polys = [r["polygon"] for r in all_patch_results]
    scores = [r["score"] for r in all_patch_results]
    kept_polys, kept_scores = nms_polygons(polys, scores, NMS_IOU_THRESHOLD)

    merged = []
    for p, s in zip(kept_polys, kept_scores):
        # Find the original prompt for this score
        prompt = "unknown"
        for r in all_patch_results:
            if abs(r["score"] - s) < 1e-6 and r["polygon"].equals(p):
                prompt = r["prompt"]
                break
        merged.append({"polygon": p, "score": s, "prompt": prompt})

    log.info(f"NMS: {len(all_patch_results)} raw -> {len(merged)} deduplicated")
    return merged


# ---------------------------------------------------------------------------
# GeoJSON output
# ---------------------------------------------------------------------------

def to_geojson(results: list, tile_id: str, region: str, metadata: dict = None) -> dict:
    """Build a GeoJSON FeatureCollection with detection metadata."""
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "features": [],
    }

    if metadata:
        fc["metadata"] = metadata

    if not results:
        return fc

    for i, r in enumerate(results):
        g = r["polygon"] if isinstance(r, dict) else r
        if g.is_empty:
            continue
        props = {
            "tile_id": tile_id,
            "region": region,
            "detection_id": i,
            "crs": "EPSG:25831",
            "area_m2": round(g.area, 2),
        }
        if isinstance(r, dict):
            props["score"] = round(r.get("score", 0.0), 3)
            props["prompt"] = r.get("prompt", "")

        fc["features"].append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": props,
        })

    return fc


def save_geojson(data: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def expected_outputs(tile_id: str, process_donut: bool) -> list[Path]:
    paths = [RAW_DIR / f"{tile_id}_core.geojson"]
    if process_donut:
        paths.append(RAW_DIR / f"{tile_id}_donut.geojson")
    return paths


def tile_already_done(tile_id: str, process_donut: bool) -> bool:
    return all(p.exists() for p in expected_outputs(tile_id, process_donut))


# ---------------------------------------------------------------------------
# Per-tile processor
# ---------------------------------------------------------------------------

def process_tile(
    tile_id: str,
    tile_geom,
    crs_epsg: int,
    ortho_path: Path,
    process_donut: bool,
    overlap_fraction: float,
    box_threshold: float,
    text_threshold: float,
    overwrite: bool,
    pipeline_config: dict,
) -> dict:
    """Run the full pipeline for one tile. Returns a summary dict."""
    summary = {"tile_id": tile_id, "core": 0, "donut": 0, "skipped": False, "already_done": False}

    if not overwrite and tile_already_done(tile_id, process_donut):
        summary["already_done"] = True
        return summary

    t0 = time.time()
    log.info(f"Processing tile {tile_id}...")

    model_versions = get_model_versions()

    # --- Core tile ---
    tmp = clip_ortho_to_geom(ortho_path, tile_geom, crs_epsg)
    if tmp is None:
        summary["skipped"] = True
        log.warning(f"Tile {tile_id}: no intersection with ortho, skipped.")
        return summary

    try:
        pil_img, transform, crs_wkt = tif_to_pil(tmp)
        img_w, img_h = pil_img.size

        log.info(f"Tile {tile_id}: image {img_w}x{img_h} px")

        results = run_langsam(
            pil_img, transform,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
            use_tiling=True,
        )

        # Extract polygons and scores
        polygons = [r["polygon"] for r in results]
        scores = [r["score"] for r in results]

        # --- Geometric filters ---
        tile_area_px = img_w * img_h
        resolution_m_per_px = 0.25  # ICGC 25cm ortho

        polygons = filter_polygons(polygons, MIN_POLYGON_AREA_M2, MAX_POLYGON_AREA_M2)
        log.info(f"  After area filter: {len(polygons)}")

        polygons = filter_aspect_ratio(polygons, MAX_ASPECT_RATIO)
        log.info(f"  After aspect ratio filter: {len(polygons)}")

        polygons = filter_tile_occupancy(polygons, tile_area_px, resolution_m_per_px, MAX_TILE_OCCUPANCY)
        log.info(f"  After occupancy filter: {len(polygons)}")

        # NMS again after geometric filters
        if polygons:
            polygons, scores = nms_polygons(polygons, scores, NMS_IOU_THRESHOLD)
            log.info(f"  After final NMS: {len(polygons)}")

        # Merge nearby
        polygons = merge_nearby_polygons(polygons, MERGE_DISTANCE_M)

        # Rebuild results with scores
        results_filtered = [{"polygon": p, "score": 1.0, "prompt": "merged"} for p in polygons]

        summary["core"] = len(results_filtered)

        # Metadata for output
        metadata = {
            "tile_id": tile_id,
            "model_versions": model_versions,
            "parameters": {
                "box_threshold": box_threshold,
                "text_threshold": text_threshold,
                "subtile_size_px": SUBTILE_SIZE_PX,
                "subtile_overlap_px": SUBTILE_OVERLAP_PX,
                "nms_iou_threshold": NMS_IOU_THRESHOLD,
                "min_polygon_area_m2": MIN_POLYGON_AREA_M2,
                "max_polygon_area_m2": MAX_POLYGON_AREA_M2,
                "max_tile_occupancy": MAX_TILE_OCCUPANCY,
                "max_aspect_ratio": MAX_ASPECT_RATIO,
                "merge_distance_m": MERGE_DISTANCE_M,
                "prompts": PV_PROMPTS,
            },
            "tile_info": {
                "image_size_px": [img_w, img_h],
                "resolution_cm": 25,
                "crs": crs_wkt,
            },
            "detection_stats": {
                "total_raw": len(results),
                "after_area_filter": len(filter_polygons([r["polygon"] for r in results], MIN_POLYGON_AREA_M2, MAX_POLYGON_AREA_M2)),
                "final": len(results_filtered),
            },
        }

        save_geojson(
            to_geojson(results_filtered, tile_id, "core", metadata),
            RAW_DIR / f"{tile_id}_core.geojson",
        )
    finally:
        tmp.unlink(missing_ok=True)
        clear_gpu_cache()

    # --- Donut (25% overlap ring) ---
    if process_donut:
        donut_geom = build_donut(tile_geom, overlap_fraction)
        tmp_d = clip_ortho_to_geom(ortho_path, donut_geom, crs_epsg)
        if tmp_d is not None:
            try:
                pil_d, transform_d, _ = tif_to_pil(tmp_d)
                results_d = run_langsam(
                    pil_d, transform_d,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                    use_tiling=True,
                )

                polygons_d = [r["polygon"] for r in results_d]
                scores_d = [r["score"] for r in results_d]

                polygons_d = filter_polygons(polygons_d, MIN_POLYGON_AREA_M2, MAX_POLYGON_AREA_M2)
                polygons_d = filter_aspect_ratio(polygons_d, MAX_ASPECT_RATIO)

                img_d_w, img_d_h = pil_d.size
                polygons_d = filter_tile_occupancy(
                    polygons_d, img_d_w * img_d_h, 0.25, MAX_TILE_OCCUPANCY
                )

                if polygons_d:
                    polygons_d, scores_d = nms_polygons(polygons_d, scores_d, NMS_IOU_THRESHOLD)

                polygons_d = merge_nearby_polygons(polygons_d, MERGE_DISTANCE_M)

                results_d_filtered = [{"polygon": p, "score": 1.0, "prompt": "merged"} for p in polygons_d]

                summary["donut"] = len(results_d_filtered)
                save_geojson(
                    to_geojson(results_d_filtered, tile_id, "donut"),
                    RAW_DIR / f"{tile_id}_donut.geojson",
                )
            finally:
                tmp_d.unlink(missing_ok=True)

    elapsed = time.time() - t0
    log.info(f"Tile {tile_id} done: core={summary['core']} polys, donut={summary['donut']} polys, {elapsed:.1f}s")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--ortho", "ortho_path", default=str(DEFAULT_ORTHO), show_default=True,
    type=click.Path(exists=True),
    help="Path to ortho mosaic GeoTIFF.",
)
@click.option(
    "--grid", "grid_path", default=str(DEFAULT_GRID), show_default=True,
    type=click.Path(exists=True),
    help="GeoJSON or GPKG/SHP with UTM 1km quadricule polygons.",
)
@click.option(
    "--tile-id", default=None,
    help="Process only this tile ID.",
)
@click.option(
    "--process-donut", is_flag=True, default=False,
    help="Also run LangSAM on the 25% overlap donut ring.",
)
@click.option(
    "--overlap-fraction", default=None, type=float,
    help="Overlap fraction for donut.",
)
@click.option(
    "--box-threshold", default=0.40, type=float, show_default=True,
    help="GroundingDINO box confidence threshold.",
)
@click.option(
    "--text-threshold", default=0.30, type=float, show_default=True,
    help="GroundingDINO text confidence threshold.",
)
@click.option(
    "--subtile-size", default=SUBTILE_SIZE_PX, type=int, show_default=True,
    help="Sub-tile size in pixels for GroundingDINO input.",
)
@click.option(
    "--subtile-overlap", default=SUBTILE_OVERLAP_PX, type=int, show_default=True,
    help="Sub-tile overlap in pixels.",
)
@click.option(
    "--no-tiling", is_flag=True, default=False,
    help="Disable sub-tiling (run on full image).",
)
@click.option(
    "--overwrite", is_flag=True, default=False,
    help="Reprocess tiles even if output exists.",
)
def main(ortho_path, grid_path, tile_id, process_donut, overlap_fraction,
         box_threshold, text_threshold, subtile_size, subtile_overlap,
         no_tiling, overwrite):
    """
    Automated PV detection on ortho tiles using LangSAM with sub-tiling.

    Clips the ortho to each UTM 1km tile, splits into 800x800 sub-tiles
    for optimal GroundingDINO detection, applies geometric filters and NMS,
    and saves georeferenced GeoJSON masks.
    """
    ortho = Path(ortho_path)
    grid = Path(grid_path)
    overlap = overlap_fraction or config.get("overlap_fraction", 0.25)

    pipeline_config = {
        "subtile_size_px": subtile_size,
        "subtile_overlap_px": subtile_overlap,
        "use_tiling": not no_tiling,
    }

    gdf = gpd.read_file(grid)
    crs_epsg = gdf.crs.to_epsg()
    tile_id_col = "COORD_1K"

    if tile_id_col not in gdf.columns:
        click.echo(f"ERROR: column '{tile_id_col}' not found in grid. "
                   f"Available columns: {gdf.columns.tolist()}")
        sys.exit(1)

    if tile_id:
        gdf = gdf[gdf[tile_id_col] == tile_id]
        if gdf.empty:
            click.echo(f"ERROR: tile '{tile_id}' not found in grid.")
            sys.exit(1)

    model_versions = get_model_versions()

    click.echo(f"\nOrtho       : {ortho}")
    click.echo(f"Grid        : {grid}  ({len(gdf)} tile(s))")
    click.echo(f"Output dir  : {RAW_DIR}")
    click.echo(f"Prompts     : {len(PV_PROMPTS)} — {PV_PROMPTS}")
    click.echo(f"Donut       : {'yes' if process_donut else 'no'}")
    click.echo(f"Overlap     : {overlap*100:.0f}%")
    click.echo(f"Box thresh  : {box_threshold}")
    click.echo(f"Text thresh : {text_threshold}")
    click.echo(f"Subtile     : {subtile_size}px, overlap {subtile_overlap}px")
    click.echo(f"Tiling      : {'disabled' if no_tiling else 'enabled'}")
    click.echo(f"NMS IoU     : {NMS_IOU_THRESHOLD}")
    click.echo(f"Max occ.    : {MAX_TILE_OCCUPANCY*100:.0f}%")
    click.echo(f"Max aspect  : {MAX_ASPECT_RATIO}")
    click.echo(f"Overwrite   : {'yes' if overwrite else 'no (resume mode)'}")
    click.echo(f"Models      : {json.dumps(model_versions, indent=2)}\n")

    summaries = []
    all_features = []
    for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Tiles"):
        t_id = row[tile_id_col]
        t_geom = row.geometry

        if not overwrite and tile_already_done(t_id, process_donut):
            summaries.append({"tile_id": t_id, "core": 0, "donut": 0,
                               "skipped": False, "already_done": True})
            continue

        click.echo(f"\n--- {t_id} ---")
        s = process_tile(
            t_id, t_geom, crs_epsg, ortho, process_donut, overlap,
            box_threshold, text_threshold, overwrite, pipeline_config,
        )
        summaries.append(s)

        core_file = RAW_DIR / f"{t_id}_core.geojson"
        if core_file.exists():
            with open(core_file) as f:
                core_geojson = json.load(f)
            all_features.extend(core_geojson["features"])

        if process_donut:
            donut_file = RAW_DIR / f"{t_id}_donut.geojson"
            if donut_file.exists():
                with open(donut_file) as f:
                    donut_geojson = json.load(f)
                all_features.extend(donut_geojson["features"])

        if s["already_done"]:
            status = "ALREADY DONE (skipped)"
        elif s["skipped"]:
            status = "SKIPPED (no intersection)"
        else:
            status = f"core={s['core']} polys  donut={s['donut']} polys"
        click.echo(f"    {status}")

    # Summary table
    click.echo("\n" + "=" * 60)
    click.echo(f"{'TILE':<20} {'CORE':>6} {'DONUT':>6} {'STATUS':>15}")
    click.echo("-" * 60)
    for s in summaries:
        if s["already_done"]:
            status = "ALREADY DONE"
        elif s["skipped"]:
            status = "SKIPPED"
        else:
            status = "OK"
        click.echo(f"{s['tile_id']:<20} {s['core']:>6} {s['donut']:>6} {status:>15}")
    click.echo("=" * 60)
    n_done = sum(1 for s in summaries if not s["already_done"] and not s["skipped"])
    n_already = sum(1 for s in summaries if s["already_done"])
    n_skipped = sum(1 for s in summaries if s["skipped"])
    click.echo(f"Processed this run: {n_done}  |  Already done (skipped): {n_already}  |  No intersection: {n_skipped}")

    # Merged output
    merged_geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "metadata": {
            "model_versions": model_versions,
            "parameters": {
                "box_threshold": box_threshold,
                "text_threshold": text_threshold,
                "subtile_size_px": subtile_size,
                "subtile_overlap_px": subtile_overlap,
                "nms_iou_threshold": NMS_IOU_THRESHOLD,
            },
        },
        "features": all_features,
    }

    merged_path = RAW_DIR / "langsam_merged.geojson"
    with open(merged_path, "w") as f:
        json.dump(merged_geojson, f, indent=2)

    click.echo(f"\nMerged GeoJSON: {merged_path}")
    click.echo(f"Output: {RAW_DIR}\n")


if __name__ == "__main__":
    main()
