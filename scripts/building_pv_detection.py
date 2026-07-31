#!/usr/bin/env python3
"""
building_pv_detection.py — Detect PV on a single building from a GeoPackage.

Pipeline:
  1. Read building geometry (fid=125534) from tarragona-roof-pv-potential.gpkg
  2. Compute centroid + bounding box → 512x512 px crop at 25cm/px (128m x 128m)
  3. Fetch ICGC 2025 orthophoto via WMS
  4. Create binary mask from building polygon (rasterize)
  5. Apply mask: non-building pixels = 0 (black)
  6. Save masked GeoTIFF
  7. Run LangSAM inference on masked crop
  8. Save georeferenced GeoJSON detections
"""

from __future__ import annotations

import json
import logging
import math
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

import fiona
import numpy as np
import rasterio
from rasterio.features import rasterize as rio_rasterize
from rasterio.transform import from_bounds
from shapely.geometry import mapping, shape
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / "modules").is_dir():
    PROJECT_ROOT = Path("/app")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BUILDING_GPKG = PROJECT_ROOT / "sandbox" / "data" / "buildings.gpkg"
if not BUILDING_GPKG.exists():
    BUILDING_GPKG = Path(__file__).resolve().parents[1] / "sandbox" / "data" / "buildings.gpkg"
BUILDING_LAYER = "buildings"
BUILDING_FID = 125534

OUTPUT_DIR = PROJECT_ROOT / "sandbox" / "data" / "building_pv"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# WMS config
WMS_URL = "https://geoserveis.icgc.cat/servei/catalunya/orto-territorial/wms"
WMS_VERSION = "1.3.0"
WMS_LAYER = "ortofoto_color_vigent"
WMS_CRS = "EPSG:25831"
ORTHO_RESOLUTION_CM = 25
ORTHO_RESOLUTION_M = ORTHO_RESOLUTION_CM / 100.0

# Crop config
CROP_SIZE_PX = 512  # 512x512 px → 128m x 128m at 25cm/px
CROP_SIZE_M = CROP_SIZE_PX * ORTHO_RESOLUTION_M  # 128m

# LangSAM params
BOX_THRESHOLD = 0.40
TEXT_THRESHOLD = 0.30


def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# Step 1: Read building geometry
# ---------------------------------------------------------------------------

def read_building(gpkg: Path, layer: str, fid: int) -> dict:
    """Read a single building feature from a GeoPackage.

    Returns dict with keys: geom (shapely), bounds, centroid, properties.
    """
    log.info(f"Reading building fid={fid} from {gpkg.name}...")

    with fiona.open(str(gpkg), layer=layer) as src:
        crs = src.crs
        for feat in src:
            feat_id = feat.get("id") or feat.get("properties", {}).get("fid")
            if str(feat_id) == str(fid):
                geom = shape(feat["geometry"])
                bounds = geom.bounds  # (minx, miny, maxx, maxy)
                centroid = geom.centroid

                log.info(f"  CRS: {crs}")
                log.info(f"  Bounds: {bounds}")
                log.info(f"  Width:  {bounds[2]-bounds[0]:.2f} m")
                log.info(f"  Height: {bounds[3]-bounds[1]:.2f} m")
                log.info(f"  Area:   {geom.area:.2f} m²")
                log.info(f"  Centroid: ({centroid.x:.2f}, {centroid.y:.2f})")

                return {
                    "geom": geom,
                    "bounds": bounds,
                    "centroid": (centroid.x, centroid.y),
                    "properties": feat["properties"],
                    "crs": crs,
                }

    raise ValueError(f"Building fid={fid} not found in layer '{layer}'")


# ---------------------------------------------------------------------------
# Step 2: Compute crop bbox
# ---------------------------------------------------------------------------

def compute_crop_bbox(centroid: tuple, crop_size_m: float) -> tuple:
    """Compute a square bbox centered on centroid.

    Returns (minx, miny, maxx, maxy) in native CRS.
    """
    cx, cy = centroid
    half = crop_size_m / 2.0
    bbox = (cx - half, cy - half, cx + half, cy + half)
    log.info(f"Crop BBOX: {bbox}")
    log.info(f"Crop size: {crop_size_m}m x {crop_size_m}m = {CROP_SIZE_PX}x{CROP_SIZE_PX} px")
    return bbox


# ---------------------------------------------------------------------------
# Step 3: Fetch ortho from WMS
# ---------------------------------------------------------------------------

def fetch_ortho_wms(bbox: tuple, width_px: int, height_px: int) -> bytes:
    """Download orthophoto from ICGC WMS.

    Args:
        bbox: (minx, miny, maxx, maxy) in EPSG:25831
        width_px: output width in pixels
        height_px: output height in pixels

    Returns raw image bytes.
    """
    minx, miny, maxx, maxy = bbox
    bbox_str = f"{minx},{miny},{maxx},{maxy}"

    params = (
        f"SERVICE=WMS&VERSION={WMS_VERSION}&REQUEST=GetMap"
        f"&LAYERS={WMS_LAYER}&STYLES="
        f"&CRS={WMS_CRS}"
        f"&BBOX={bbox_str}"
        f"&WIDTH={width_px}&HEIGHT={height_px}"
        f"&FORMAT=image/tiff"
        f"&TRANSPARENT=FALSE"
    )
    url = f"{WMS_URL}?{params}"

    log.info(f"WMS URL length: {len(url)} chars")
    log.info(f"Fetching ortho: {width_px}x{height_px} px, BBOX={bbox_str}")

    ctx = ssl_ctx()
    try:
        resp = urllib.request.urlopen(url, timeout=60, context=ctx)
        data = resp.read()
    except Exception as e:
        raise ConnectionError(f"WMS download failed: {e}")

    if len(data) < 1000:
        try:
            error_text = data.decode("utf-8", errors="replace")
            if "ServiceException" in error_text:
                raise ValueError(f"WMS error: {error_text[:500]}")
        except (UnicodeDecodeError, ValueError):
            pass
        raise ValueError(f"WMS returned suspiciously small response ({len(data)} bytes)")

    log.info(f"Downloaded: {len(data)/1024:.1f} KB")
    return data


# ---------------------------------------------------------------------------
# Step 4 & 5: Create mask and apply to ortho
# ---------------------------------------------------------------------------

def create_masked_geotiff(
    ortho_bytes: bytes,
    building_geom,
    crop_bbox: tuple,
    crop_size_px: int,
    output_path: Path,
) -> Path:
    """Create a GeoTIFF with non-building areas masked out (set to 0).

    Args:
        ortho_bytes: raw GeoTIFF bytes from WMS
        building_geom: shapely geometry of the building (in EPSG:25831)
        crop_bbox: (minx, miny, maxx, maxy) in EPSG:25831
        crop_size_px: output pixel size (square)
        output_path: where to save the masked GeoTIFF

    Returns path to saved file.
    """
    minx, miny, maxx, maxy = crop_bbox
    res = ORTHO_RESOLUTION_M

    # Read the downloaded ortho
    with rasterio.open(ortho_bytes) as src:
        ortho_data = src.read()  # (bands, H, W)
        ortho_meta = src.meta.copy()
        log.info(f"Ortho loaded: {src.width}x{src.height}, {src.count} bands, CRS={src.crs}")

    # Create binary mask from building polygon
    # The mask raster must align with the ortho grid
    mask_transform = from_bounds(minx, miny, maxx, maxy, crop_size_px, crop_size_px)

    mask_shapes = [(mapping(building_geom), 1)]
    mask = rio_rasterize(
        mask_shapes,
        out_shape=(crop_size_px, crop_size_px),
        transform=mask_transform,
        fill=0,
        dtype=np.uint8,
    )

    building_pixels = np.count_nonzero(mask)
    total_pixels = crop_size_px * crop_size_px
    log.info(f"Building mask: {building_pixels}/{total_pixels} pixels ({100*building_pixels/total_pixels:.1f}%)")

    # Resize ortho if needed to match crop_size_px
    if ortho_data.shape[1] != crop_size_px or ortho_data.shape[2] != crop_size_px:
        from PIL import Image as PILImage

        log.info(f"Resizing ortho from {ortho_data.shape[2]}x{ortho_data.shape[1]} to {crop_size_px}x{crop_size_px}")
        resized_bands = []
        for b in range(ortho_data.shape[0]):
            band_img = PILImage.fromarray(ortho_data[b])
            band_img = band_img.resize((crop_size_px, crop_size_px), PILImage.LANCZOS)
            resized_bands.append(np.array(band_img))
        ortho_data = np.stack(resized_bands)

    # Apply mask: set non-building pixels to 0
    mask_3d = np.broadcast_to(mask[np.newaxis, :, :], ortho_data.shape)
    masked_data = ortho_data.copy()
    masked_data[mask_3d == 0] = 0

    # Write output GeoTIFF
    meta = ortho_meta.copy()
    meta.update(
        driver="GTiff",
        height=crop_size_px,
        width=crop_size_px,
        transform=mask_transform,
        count=ortho_data.shape[0],
    )

    # Ensure 8-bit output
    if masked_data.dtype != np.uint8:
        if masked_data.max() > 255:
            masked_data = (masked_data / masked_data.max() * 255).astype(np.uint8)
        else:
            masked_data = masked_data.astype(np.uint8)

    with rasterio.open(str(output_path), "w", **meta) as dst:
        dst.write(masked_data)

    log.info(f"Saved masked GeoTIFF: {output_path} ({output_path.stat().st_size/1024:.1f} KB)")
    return output_path


# ---------------------------------------------------------------------------
# Step 6: Also save unmasked ortho for visual comparison
# ---------------------------------------------------------------------------

def save_unmasked_ortho(ortho_bytes: bytes, crop_bbox: tuple, crop_size_px: int, output_path: Path) -> Path:
    """Save the raw orthophoto without mask for comparison."""
    minx, miny, maxx, maxy = crop_bbox
    res = ORTHO_RESOLUTION_M

    with rasterio.open(ortho_bytes) as src:
        ortho_data = src.read()
        ortho_meta = src.meta.copy()

    transform = from_bounds(minx, miny, maxx, maxy, crop_size_px, crop_size_px)

    if ortho_data.shape[1] != crop_size_px or ortho_data.shape[2] != crop_size_px:
        from PIL import Image as PILImage
        resized_bands = []
        for b in range(ortho_data.shape[0]):
            band_img = PILImage.fromarray(ortho_data[b])
            band_img = band_img.resize((crop_size_px, crop_size_px), PILImage.LANCZOS)
            resized_bands.append(np.array(band_img))
        ortho_data = np.stack(resized_bands)

    if ortho_data.dtype != np.uint8:
        if ortho_data.max() > 255:
            ortho_data = (ortho_data / ortho_data.max() * 255).astype(np.uint8)
        else:
            ortho_data = ortho_data.astype(np.uint8)

    meta = ortho_meta.copy()
    meta.update(
        driver="GTiff",
        height=crop_size_px,
        width=crop_size_px,
        transform=transform,
        count=ortho_data.shape[0],
    )

    with rasterio.open(str(output_path), "w", **meta) as dst:
        dst.write(ortho_data)

    log.info(f"Saved unmasked ortho: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Step 7: Run LangSAM
# ---------------------------------------------------------------------------

def run_langsam_detection(tif_path: Path) -> list[dict]:
    """Run LangSAM on a GeoTIFF and return detection results."""
    sys.path.insert(0, str(PROJECT_ROOT))

    from modules.langsam.langsam import (
        tif_to_pil,
        run_langsam,
        PV_PROMPTS,
    )

    log.info(f"Running LangSAM on {tif_path.name}...")

    pil_img, transform, crs_wkt = tif_to_pil(tif_path)
    img_w, img_h = pil_img.size
    log.info(f"Image: {img_w}x{img_h} px, CRS: {crs_wkt[:50]}...")

    results = run_langsam(
        pil_img,
        transform,
        box_threshold=BOX_THRESHOLD,
        text_threshold=TEXT_THRESHOLD,
        use_tiling=False,  # 512x512 fits in a single sub-tile
    )

    log.info(f"Raw detections: {len(results)}")
    for i, r in enumerate(results):
        log.info(f"  [{i}] score={r['score']:.3f} prompt='{r['prompt']}' area={r['polygon'].area:.2f}m²")

    return results


# ---------------------------------------------------------------------------
# Step 8: Save GeoJSON
# ---------------------------------------------------------------------------

def save_results_geojson(
    results: list[dict],
    building_props: dict,
    crop_bbox: tuple,
    output_path: Path,
) -> None:
    """Save LangSAM detections as georeferenced GeoJSON."""
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "metadata": {
            "building_fid": BUILDING_FID,
            "building_reference": building_props.get("reference", ""),
            "building_use": building_props.get("currentUse", ""),
            "building_area_m2": building_props.get("building_area", 0),
            "crop_bbox": list(crop_bbox),
            "crop_size_px": CROP_SIZE_PX,
            "resolution_cm": ORTHO_RESOLUTION_CM,
            "parameters": {
                "box_threshold": BOX_THRESHOLD,
                "text_threshold": TEXT_THRESHOLD,
            },
        },
        "features": [],
    }

    for i, r in enumerate(results):
        g = r["polygon"]
        if g.is_empty:
            continue

        # Convert geometry from pixel-like coords to real CRS
        # The polygons from pixel_masks_to_polygons are already georeferenced
        props = {
            "detection_id": i,
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

    with open(output_path, "w") as f:
        json.dump(fc, f, indent=2)

    log.info(f"Saved {len(fc['features'])} detections to {output_path}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    log.info("=" * 60)
    log.info("Building PV Detection Pipeline")
    log.info("=" * 60)

    # Step 1: Read building
    building = read_building(BUILDING_GPKG, BUILDING_LAYER, BUILDING_FID)
    geom = building["geom"]
    centroid = building["centroid"]
    bounds = building["bounds"]

    # Step 2: Compute crop bbox (centered on centroid, 128m x 128m)
    crop_bbox = compute_crop_bbox(centroid, CROP_SIZE_M)

    # Step 3: Fetch ortho from WMS
    ortho_bytes = fetch_ortho_wms(crop_bbox, CROP_SIZE_PX, CROP_SIZE_PX)

    # Save raw ortho for reference
    raw_path = OUTPUT_DIR / f"building_{BUILDING_FID}_ortho_raw.tif"
    save_unmasked_ortho(ortho_bytes, crop_bbox, CROP_SIZE_PX, raw_path)

    # Steps 4-5: Create mask and save masked GeoTIFF
    masked_path = OUTPUT_DIR / f"building_{BUILDING_FID}_masked.tif"
    create_masked_geotiff(ortho_bytes, geom, crop_bbox, CROP_SIZE_PX, masked_path)

    # Step 7: Run LangSAM
    results = run_langsam_detection(masked_path)

    # Step 8: Save results
    geojson_path = OUTPUT_DIR / f"building_{BUILDING_FID}_pv_detections.geojson"
    save_results_geojson(results, building["properties"], crop_bbox, geojson_path)

    elapsed = time.time() - t0

    log.info("")
    log.info("=" * 60)
    log.info(f"PIPELINE COMPLETE in {elapsed:.1f}s")
    log.info(f"Building:     fid={BUILDING_FID} ({building['properties'].get('reference', 'N/A')})")
    log.info(f"Raw ortho:    {raw_path}")
    log.info(f"Masked ortho: {masked_path}")
    log.info(f"Detections:   {len(results)} PV polygons")
    log.info(f"GeoJSON:      {geojson_path}")
    log.info("=" * 60)

    if results:
        log.info("\nDetections summary:")
        for i, r in enumerate(results):
            g = r["polygon"]
            log.info(f"  [{i}] score={r['score']:.3f} area={g.area:.2f}m² prompt='{r['prompt']}'")
    else:
        log.info("\nNo PV panels detected on this building.")


if __name__ == "__main__":
    main()
