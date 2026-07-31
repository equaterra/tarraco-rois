#!/usr/bin/env python3
"""
Phase 3: PV Detection via GroundingDINO only (no SAM) with semantic negative masks.

Approach:
1. Load GroundingDINO via LangSAM (gdino sub-model, no SAM inference)
2. Run PV prompts → get PV bounding boxes
3. Run negative prompts (roof tile, grass, shadow, tree, etc.) → get negative boxes
4. Filter: keep PV boxes that don't significantly overlap with any negative box
5. Also clip to building footprint (the masked TIF already enforces this)
6. Output remaining bounding boxes as GeoJSON

This is orders of magnitude faster than SAM since we skip the image encoder.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from shapely.geometry import box as shapely_box, mapping, shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / "modules").is_dir():
    PROJECT_ROOT = Path("/app")
sys.path.insert(0, str(PROJECT_ROOT))

from modules.langsam.langsam import get_model

# --- PV prompts (original photovoltaic semantics) ---
PV_PROMPTS = [
    "solar panel",
    "photovoltaic panel",
    "solar farm",
    "photovoltaic array",
    "rooftop solar panels",
]

# --- Negative semantic prompts (exclusion zones) ---
NEGATIVE_PROMPTS = [
    "roof tile",
    "grass",
    "shadow",
    "tree",
    "vegetation",
    "sky",
]

BOX_THRESHOLD = 0.30
TEXT_THRESHOLD = 0.25

# Overlap ratio threshold: if a PV box overlaps a negative box
# by more than this fraction of the PV box area, it's rejected
NEGATIVE_OVERLAP_THRESHOLD = 0.3

MASKED_TIF = PROJECT_ROOT / "sandbox/data/building_pv/building_125534_masked.tif"
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


def boxes_to_polygons(boxes: np.ndarray, transform: Affine) -> list:
    """Convert pixel-space boxes (x1,y1,x2,y2) to georeferenced Shapely polygons."""
    polys = []
    for box in boxes:
        x1, y1, x2, y2 = box
        # Pixel corners → geo coordinates
        x1g, y1g = transform * (x1, y1)
        x2g, y2g = transform * (x2, y2)
        poly = shapely_box(min(x1g, x2g), min(y1g, y2g), max(x1g, x2g), max(y1g, y2g))
        polys.append(poly)
    return polys


def main():
    t0 = time.time()
    print("=" * 60)
    print("Phase 3: GroundingDINO-only PV Detection")
    print("  Building: fid=125534")
    print("  Strategy: GDINO boxes (no SAM) + semantic negative masks")
    print("=" * 60)

    # Load masked GeoTIFF
    print(f"\nLoading masked GeoTIFF: {MASKED_TIF}")
    pil_img, transform, crs_wkt = tif_to_pil(MASKED_TIF)
    img_w, img_h = pil_img.size
    res_m = 0.25
    print(f"  Image: {img_w}x{img_h} px = {img_w*res_m:.0f}m x {img_h*res_m:.0f}m")

    # Load building footprint
    with open(str(GEOM_JSON)) as f:
        building_feat = json.load(f)
    building_geom = shape(building_feat["geometry"])

    # Load LangSAM model (we only use the gdino sub-model)
    print("\nLoading GroundingDINO model...")
    model = get_model()
    gdino = model.gdino
    print("  GroundingDINO loaded.")

    # --- Step 1: Run PV prompts ---
    print(f"\n[1] Running PV prompts ({len(PV_PROMPTS)})...")
    all_pv_results = []
    for prompt in PV_PROMPTS:
        t_prompt = time.time()
        results = gdino.predict(
            [pil_img], [prompt],
            box_threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
        )
        elapsed = time.time() - t_prompt

        for result in results:
            boxes = result.get("boxes")
            scores = result.get("scores")
            labels = result.get("labels", [])
            if boxes is None or len(boxes) == 0:
                print(f"  '{prompt}': no detections ({elapsed:.1f}s)")
                continue

            n = len(boxes)
            polys = boxes_to_polygons(boxes, transform)
            print(f"  '{prompt}': {n} box(es) ({elapsed:.1f}s)")
            for k in range(n):
                score = float(scores[k]) if scores is not None and k < len(scores) else 1.0
                label = labels[k] if labels and k < len(labels) else prompt
                all_pv_results.append({
                    "polygon": polys[k],
                    "score": score,
                    "prompt": label,
                    "box_pixels": boxes[k].tolist(),
                })

    print(f"\n  Total PV detections: {len(all_pv_results)}")

    # --- Step 2: Run negative prompts ---
    print(f"\n[2] Running negative prompts ({len(NEGATIVE_PROMPTS)})...")
    all_negative_polys = []
    for prompt in NEGATIVE_PROMPTS:
        t_prompt = time.time()
        results = gdino.predict(
            [pil_img], [prompt],
            box_threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
        )
        elapsed = time.time() - t_prompt

        for result in results:
            boxes = result.get("boxes")
            scores = result.get("scores")
            labels = result.get("labels", [])
            if boxes is None or len(boxes) == 0:
                print(f"  '{prompt}': no detections ({elapsed:.1f}s)")
                continue

            n = len(boxes)
            polys = boxes_to_polygons(boxes, transform)
            print(f"  '{prompt}': {n} box(es) ({elapsed:.1f}s)")
            for k in range(n):
                label = labels[k] if labels and k < len(labels) else prompt
                all_negative_polys.append({
                    "polygon": polys[k],
                    "score": float(scores[k]) if scores is not None and k < len(scores) else 1.0,
                    "prompt": label,
                })

    print(f"\n  Total negative detections: {len(all_negative_polys)}")

    # --- Step 3: Filter PV boxes using negative masks ---
    print(f"\n[3] Applying semantic negative masks...")
    if not all_pv_results:
        print("  (no PV detections to filter)")
        save_geojson([], building_feat, all_negative_polys, OUT_DIR / "building_125534_pv_detections.geojson")
        return

    kept = []
    rejected_by_negative = 0
    rejected_by_building = 0

    for r in all_pv_results:
        pv_poly = r["polygon"]

        # Check building footprint (already masked in TIF, but double-check)
        if not pv_poly.intersects(building_geom):
            rejected_by_building += 1
            continue

        # Check overlap with negative detections
        rejected = False
        for neg in all_negative_polys:
            neg_poly = neg["polygon"]
            if pv_poly.intersects(neg_poly):
                inter = pv_poly.intersection(neg_poly).area
                pv_area = pv_poly.area
                if pv_area > 0 and (inter / pv_area) > NEGATIVE_OVERLAP_THRESHOLD:
                    rejected = True
                    break

        if rejected:
            rejected_by_negative += 1
            continue

        kept.append(r)

    print(f"  Kept: {len(kept)}")
    print(f"  Rejected (negative mask overlap): {rejected_by_negative}")
    print(f"  Rejected (outside building): {rejected_by_building}")

    # --- Step 4: Save GeoJSON ---
    save_geojson(kept, building_feat, all_negative_polys,
                 OUT_DIR / "building_125534_pv_detections.geojson")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"COMPLETE in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"PV detections (raw): {len(all_pv_results)}")
    print(f"Negative detections: {len(all_negative_polys)}")
    print(f"PV detections (kept): {len(kept)}")
    if kept:
        for i, r in enumerate(kept):
            g = r["polygon"]
            b = g.bounds
            print(f"  [{i}] score={r['score']:.3f} area={g.area:.1f}m2 "
                  f"size={b[2]-b[0]:.1f}x{b[3]-b[1]:.1f}m prompt='{r['prompt']}'")
    print(f"Output: {OUT_DIR / 'building_125534_pv_detections.geojson'}")
    print(f"{'='*60}")


def save_geojson(pv_results, building_feat, negative_results, output_path):
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
            "detection_method": "groundingdino_only",
            "parameters": {
                "box_threshold": BOX_THRESHOLD,
                "text_threshold": TEXT_THRESHOLD,
                "negative_overlap_threshold": NEGATIVE_OVERLAP_THRESHOLD,
                "pv_prompts": PV_PROMPTS,
                "negative_prompts": NEGATIVE_PROMPTS,
            },
        },
        "features": [],
    }

    # PV detections
    for i, r in enumerate(pv_results):
        g = r["polygon"]
        if g.is_empty:
            continue
        b = g.bounds
        props = {
            "detection_id": i,
            "type": "pv",
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

    # Negative detections (as reference, not used for PV)
    for i, r in enumerate(negative_results):
        g = r["polygon"]
        if g.is_empty:
            continue
        props = {
            "detection_id": len(pv_results) + i,
            "type": "negative_mask",
            "crs": "EPSG:25831",
            "area_m2": round(g.area, 4),
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
    print(f"\nSaved {sum(1 for f in fc['features'] if f['properties']['type']=='pv')} PV + "
          f"{sum(1 for f in fc['features'] if f['properties']['type']=='negative_mask')} negative "
          f"detections to {output_path}")


if __name__ == "__main__":
    main()
