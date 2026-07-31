#!/usr/bin/env python3
"""
Phase 4: GroundingDINO-only PV detection with sub-tiling.
Smaller tiles = panels proportionally larger in GroundingDINO input.
No SAM, no negative masks — just bounding boxes.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from shapely.geometry import box as shp_box, mapping, shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / "modules").is_dir():
    PROJECT_ROOT = Path("/app")
sys.path.insert(0, str(PROJECT_ROOT))

from modules.langsam.langsam import get_model, create_patches

PV_PROMPTS = [
    "solar panel",
    "photovoltaic panel",
    "rooftop solar panels",
    "photovoltaic array",
    "solar farm",
]

BOX_THRESHOLD = 0.30
TEXT_THRESHOLD = 0.25

SUBTILE_SIZE = 192
SUBTILE_OVERLAP = 64

MASKED_TIF = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_masked.tif"
GEOM_JSON = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_geom.json"
OUT_DIR = PROJECT_ROOT / "sandbox/data/building_pv"


def tif_to_pil(tif_path: Path) -> tuple:
    with rasterio.open(str(tif_path)) as src:
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


def boxes_to_polys(boxes: np.ndarray, transform: Affine) -> list:
    polys = []
    for box in boxes:
        x1, y1, x2, y2 = box
        x1g, y1g = transform * (x1, y1)
        x2g, y2g = transform * (x2, y2)
        poly = shp_box(min(x1g, x2g), min(y1g, y2g), max(x1g, x2g), max(y1g, y2g))
        polys.append(poly)
    return polys


def main():
    t0 = time.time()
    res_m = 0.25
    print("=" * 60)
    print("Phase 4: GDINO sub-tiling PV detection")
    print(f"  Tile size: {SUBTILE_SIZE}x{SUBTILE_SIZE}, overlap: {SUBTILE_OVERLAP}px")
    print(f"  Prompts: {len(PV_PROMPTS)}")
    print("=" * 60)

    pil_img, transform, _ = tif_to_pil(MASKED_TIF)
    img_w, img_h = pil_img.size
    print(f"\nImage: {img_w}x{img_h}px = {img_w*res_m:.0f}x{img_h*res_m:.0f}m at 25cm/px")

    with open(str(GEOM_JSON)) as f:
        building_feat = json.load(f)
    building_geom = shape(building_feat["geometry"])

    # Create sub-tiles
    patches = create_patches(pil_img, SUBTILE_SIZE, SUBTILE_OVERLAP)
    print(f"Sub-tiles: {len(patches)} ({SUBTILE_SIZE}px, {SUBTILE_OVERLAP}px overlap)")
    for i, p in enumerate(patches):
        gx, gy = p["offset_x"] * res_m, p["offset_y"] * res_m
        pw, ph = p["width"] * res_m, p["height"] * res_m
        print(f"  Tile {i}: ({gx:.0f},{gy:.0f}) -> ({gx+pw:.0f},{gy+ph:.0f})m  "
              f"px=[{p['offset_x']},{p['offset_y']},{p['offset_x']+p['width']},{p['offset_y']+p['height']}]")

    # Load model
    print("\nLoading GroundingDINO...")
    model = get_model()
    gdino = model.gdino
    print("  Loaded.")

    # Run per-tile inference
    all_detections = []
    for tile_idx, patch in enumerate(patches):
        patch_img = patch["patch"]
        px, py = patch["offset_x"], patch["offset_y"]
        patch_transform = transform * Affine.translation(px, py)

        for prompt in PV_PROMPTS:
            results = gdino.predict(
                [patch_img], [prompt],
                box_threshold=BOX_THRESHOLD,
                text_threshold=TEXT_THRESHOLD,
            )
            for r in results:
                boxes = r.get("boxes")
                scores = r.get("scores")
                if boxes is None or len(boxes) == 0:
                    continue
                polys = boxes_to_polys(boxes, patch_transform)
                for k, poly in enumerate(polys):
                    all_detections.append({
                        "polygon": poly,
                        "score": float(scores[k]) if scores is not None and k < len(scores) else 1.0,
                        "prompt": prompt,
                        "tile": tile_idx,
                    })

    if not all_detections:
        print("No detections found.")
        save_geojson([], building_feat, OUT_DIR / "building_125534_pv_detections.geojson")
        return

    # Debug: show raw areas
    print(f"\nRaw detections intersecting building:")
    intersects_count = 0
    for d in all_detections:
        poly = d["polygon"]
        touches = poly.intersects(building_geom)
        if touches:
            intersects_count += 1
            if intersects_count <= 20:
                print(f"  area={poly.area:.1f}m2 score={d['score']:.3f} prompt='{d['prompt']}'")
    print(f"  Total intersecting building: {intersects_count} / {len(all_detections)}")

    # Filter: keep rectangle boxes that touch the building and are panel-sized
    kept_all = []
    for d in all_detections:
        poly = d["polygon"]
        if not poly.intersects(building_geom):
            continue
        area = poly.area
        if area < 0.5 or area > 2000:
            continue
        kept_all.append({
            "polygon": poly,
            "score": d["score"],
            "prompt": d["prompt"],
        })

    # NMS among kept
    from modules.langsam.langsam import nms_polygons
    polys = [d["polygon"] for d in kept_all]
    scores = [d["score"] for d in kept_all]
    polys, scores = nms_polygons(polys, scores, 0.5)
    # Rebuild with scores
    kept = [{"polygon": p, "score": s, "prompt": "filtered"} for p, s in zip(polys, scores)]

    save_geojson(kept, building_feat, OUT_DIR / "building_125534_pv_detections.geojson")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"COMPLETE in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Total raw detections: {len(all_detections)}")
    print(f"After building+area filter: {len(kept_all)}")
    print(f"After NMS: {len(kept)}")
    if kept:
        total_area = sum(d["polygon"].area for d in kept)
        print(f"Total PV area: {total_area:.1f} m2")
        for i, d in enumerate(kept):
            g = d["polygon"]
            b = g.bounds
            print(f"  [{i}] score={d['score']:.3f} area={g.area:.1f}m2 "
                  f"size={b[2]-b[0]:.1f}x{b[3]-b[1]:.1f}m")
    print(f"{'='*60}")


def save_geojson(results, building_feat, output_path):
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "metadata": {
            "building_fid": 125534,
            "building_reference": building_feat.get("properties", {}).get("reference", ""),
            "crop_size_px": 512,
            "resolution_cm": 25,
            "subtile_size_px": SUBTILE_SIZE,
            "subtile_overlap_px": SUBTILE_OVERLAP,
            "parameters": {
                "box_threshold": BOX_THRESHOLD,
                "text_threshold": TEXT_THRESHOLD,
                "prompts": PV_PROMPTS,
            },
        },
        "features": [],
    }
    for i, r in enumerate(results):
        g = r["polygon"]
        if g.is_empty:
            continue
        b = g.bounds
        fc["features"].append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {
                "detection_id": i,
                "area_m2": round(g.area, 2),
                "width_m": round(b[2] - b[0], 2),
                "height_m": round(b[3] - b[1], 2),
                "score": round(r["score"], 4),
                "prompt": r.get("prompt", ""),
            },
        })
    with open(str(output_path), "w") as f:
        json.dump(fc, f, indent=2)
    print(f"Saved {len(fc['features'])} detections to {output_path}")


if __name__ == "__main__":
    main()
