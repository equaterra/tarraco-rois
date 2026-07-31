#!/usr/bin/env python3
"""
Phase 2: Run LangSAM inference on the masked building GeoTIFF.
Detects individual PV panels using strict thresholds and singular prompts.

Strategy:
- Use the full 512x512 masked image (no sub-tiling — too slow on CPU)
- Singular prompts to target individual panels, not arrays/buildings
- High thresholds (0.55/0.45) to be very selective
- Strict geometric filters: panel-sized (0.5-10 m²), low aspect ratio
- Intersect detections with building footprint to reject anything outside
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from shapely.geometry import mapping, shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / "modules").is_dir():
    PROJECT_ROOT = Path("/app")
sys.path.insert(0, str(PROJECT_ROOT))

from modules.langsam.langsam import (
    get_model,
    pixel_masks_to_polygons,
    filter_polygons,
    filter_aspect_ratio,
    nms_polygons,
    get_model_versions,
)

# Strict parameters for individual panel detection
BOX_THRESHOLD = 0.55
TEXT_THRESHOLD = 0.45

# Panel-specific prompts (singular, to avoid arrays/buildings)
PANEL_PROMPTS = [
    "one solar panel.",
    "single solar panel.",
    "one rooftop solar panel.",
]

# Geometric filters for individual PV panels
MIN_PANEL_AREA_M2 = 0.5
MAX_PANEL_AREA_M2 = 10.0
MAX_ASPECT_RATIO = 4.0
NMS_IOU = 0.3

MASKED_TIF = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_masked.tif"
RAW_TIF = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_ortho_raw.tif"
GEOM_JSON = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_geom.json"
OUT_DIR = PROJECT_ROOT / "sandbox/data/building_pv"


def tif_to_pil(tif_path: Path) -> tuple:
    """Read GeoTIFF -> (PIL RGB, Affine transform, CRS WKT)."""
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


def main():
    t0 = time.time()
    print("=" * 60)
    print("Phase 2: Individual PV Panel Detection")
    print("  Building: fid=125534")
    print(f"  Thresholds: box={BOX_THRESHOLD}, text={TEXT_THRESHOLD}")
    print(f"  Prompts: {PANEL_PROMPTS}")
    print(f"  Area filter: {MIN_PANEL_AREA_M2}-{MAX_PANEL_AREA_M2} m2")
    print("=" * 60)

    # Load masked GeoTIFF
    print(f"\nLoading masked GeoTIFF: {MASKED_TIF}")
    pil_img, transform, crs_wkt = tif_to_pil(MASKED_TIF)
    img_w, img_h = pil_img.size
    res_m = 0.25  # 25cm/px
    print(f"  Image: {img_w}x{img_h} px = {img_w*res_m:.0f}m x {img_h*res_m:.0f}m")

    # Load building footprint for intersection filter
    print(f"\nLoading building footprint: {GEOM_JSON}")
    with open(str(GEOM_JSON)) as f:
        building_feat = json.load(f)
    building_geom = shape(building_feat["geometry"])
    print(f"  Building area: {building_geom.area:.1f} m2")
    print(f"  Building bounds: {building_geom.bounds}")

    # Load model
    print("\nLoading LangSAM model...")
    model = get_model()
    model_versions = get_model_versions()
    print(f"  Versions: torch={model_versions.get('torch','?')}, "
          f"transformers={model_versions.get('transformers','?')}")

    # Run inference with each prompt
    all_results = []
    print(f"\nRunning inference ({len(PANEL_PROMPTS)} prompts x 1 image)...")

    for prompt in PANEL_PROMPTS:
        print(f"\n  Prompt: '{prompt}'")
        t_prompt = time.time()

        results = model.predict(
            [pil_img], [prompt],
            box_threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
        )
        elapsed = time.time() - t_prompt

        for result in results:
            masks = result.get("masks")
            scores = result.get("scores")
            if masks is None or len(masks) == 0:
                print(f"    No detections ({elapsed:.1f}s)")
                continue
            masks_np = np.asarray(masks, dtype=bool)
            polys = pixel_masks_to_polygons(masks_np, transform)
            print(f"    Raw detections: {len(polys)} ({elapsed:.1f}s)")
            for k, poly in enumerate(polys):
                score = scores[k] if scores is not None and k < len(scores) else 1.0
                all_results.append({
                    "polygon": poly,
                    "score": float(score),
                    "prompt": prompt,
                })

    print(f"\n{'='*60}")
    print(f"Total raw detections: {len(all_results)}")

    if not all_results:
        print("\nNo PV panels detected.")
        save_geojson([], building_feat, model_versions, 0, OUT_DIR / "building_125534_pv_detections.geojson")
        return

    # Show raw detections before filtering
    print("\nRaw detections (before filters):")
    for i, r in enumerate(all_results):
        g = r["polygon"]
        print(f"  [{i}] score={r['score']:.3f} area={g.area:.2f}m2 "
              f"bounds=[{g.bounds[0]:.1f},{g.bounds[1]:.1f},{g.bounds[2]:.1f},{g.bounds[3]:.1f}] "
              f"prompt='{r['prompt']}'")

    # NMS first
    polygons = [r["polygon"] for r in all_results]
    scores = [r["score"] for r in all_results]

    print(f"\nNMS (IoU={NMS_IOU})...")
    polys_before = len(polygons)
    polygons, scores = nms_polygons(polygons, scores, NMS_IOU)
    print(f"  {len(polygons)} (was {polys_before})")

    # Area filter
    print(f"\nArea filter ({MIN_PANEL_AREA_M2}-{MAX_PANEL_AREA_M2} m2)...")
    polys_before = len(polygons)
    polygons = filter_polygons(polygons, MIN_PANEL_AREA_M2, MAX_PANEL_AREA_M2)
    print(f"  {len(polygons)} (was {polys_before})")

    # Aspect ratio filter
    print(f"Aspect ratio filter (max {MAX_ASPECT_RATIO})...")
    polys_before = len(polygons)
    polygons = filter_aspect_ratio(polygons, MAX_ASPECT_RATIO)
    print(f"  {len(polygons)} (was {polys_before})")

    # Building intersection filter — reject detections outside building
    print(f"Building intersection filter...")
    polys_before = len(polygons)
    building_buffer = building_geom.buffer(2.0)  # 2m tolerance
    polygons = [p for p in polygons if p.intersects(building_buffer)]
    print(f"  {len(polygons)} (was {polys_before})")

    # Build results
    results_filtered = [{"polygon": p, "score": s, "prompt": "filtered"} for p, s in zip(polygons, scores)]

    # Save GeoJSON
    save_geojson(results_filtered, building_feat, model_versions, len(all_results),
                 OUT_DIR / "building_125534_pv_detections.geojson")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"COMPLETE in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Raw detections: {len(all_results)}")
    print(f"After filters:  {len(results_filtered)}")
    if results_filtered:
        total_area = sum(r["polygon"].area for r in results_filtered)
        print(f"Total PV area:  {total_area:.1f} m2")
        for i, r in enumerate(results_filtered):
            g = r["polygon"]
            b = g.bounds
            print(f"  [{i}] score={r['score']:.3f} area={g.area:.2f}m2 "
                  f"size={b[2]-b[0]:.1f}x{b[3]-b[1]:.1f}m")
    else:
        print("\n  NOTE: All detections were filtered out.")
        print("  This suggests the model cannot distinguish individual panels")
        print("  from the building at this resolution (25cm/px, 512x512 crop).")
        print("  Possible next steps:")
        print("    - Use higher-resolution imagery if available")
        print("    - Try a two-stage approach: detect roof -> subdivide into panels")
        print("    - Lower thresholds and accept some false positives")
    print(f"Output: {OUT_DIR / 'building_125534_pv_detections.geojson'}")
    print(f"{'='*60}")


def save_geojson(results, building_feat, model_versions, total_raw, output_path):
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "metadata": {
            "building_fid": 125534,
            "building_reference": building_feat.get("properties", {}).get("reference", ""),
            "building_use": building_feat.get("properties", {}).get("currentUse", ""),
            "building_area_m2": building_feat.get("properties", {}).get("building_area", 0),
            "crop_size_px": 512,
            "resolution_cm": 25,
            "parameters": {
                "box_threshold": BOX_THRESHOLD,
                "text_threshold": TEXT_THRESHOLD,
                "min_area_m2": MIN_PANEL_AREA_M2,
                "max_area_m2": MAX_PANEL_AREA_M2,
                "max_aspect_ratio": MAX_ASPECT_RATIO,
                "nms_iou": NMS_IOU,
                "prompts": PANEL_PROMPTS,
            },
            "model_versions": model_versions,
            "detection_stats": {
                "total_raw": total_raw,
                "after_filters": len(results),
            },
        },
        "features": [],
    }

    for i, r in enumerate(results):
        g = r["polygon"]
        if g.is_empty:
            continue
        b = g.bounds
        props = {
            "detection_id": i,
            "crs": "EPSG:25831",
            "area_m2": round(g.area, 4),
            "width_m": round(b[2] - b[0], 2),
            "height_m": round(b[3] - b[1], 2),
            "score": round(r.get("score", 0.0), 4),
            "prompt": r.get("prompt", ""),
        }
        fc["features"].append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": props,
        })

    with open(str(output_path), "w") as f:
        json.dump(fc, f, indent=2)
    print(f"\nSaved {len(fc['features'])} detections to {output_path}")


if __name__ == "__main__":
    main()
