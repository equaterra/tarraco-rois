#!/usr/bin/env python3
"""
run_langsam.py — Automated PV segmentation using Language Segment-Anything

Workflow
--------
  1. Read a large ortho mosaic (GeoTIFF / GPKG) from the Seagate drive.
  2. Load a grid of UTM 1km quadricules (GeoJSON / GPKG).
  3. For each quadricule that spatially intersects the ortho:
       a. Clip the ortho to the quadricule bbox  → core tile
       b. Optionally also clip the 25 % overlap donut ring → donut tile
       c. Run LangSAM with all PV text prompts on each tile
       d. Convert pixel masks → georeferenced polygons (EPSG:25831)
       e. Write  data/masks/raw/<TILE_ID>_core.geojson
                 data/masks/raw/<TILE_ID>_donut.geojson  (if --process-donut)
  4. Print a summary table (tile, detections, skipped).

This script is designed to scale from 2 test tiles to hundreds/thousands of
tiles across Catalonia without any manual clipping.

RESUME SUPPORT
--------------
Before processing a tile, the script checks whether the expected output
file(s) already exist in data/masks/raw/. If so, the tile is skipped
(status "ALREADY DONE") unless --overwrite is passed. This makes long,
multi-day/multi-week runs safe to interrupt (Ctrl+C, container stop,
computer reboot) and resume later with the exact same command.

Usage
-----
  # Full country run (resumable — safe to stop and restart):
  python run_langsam.py --ortho /path/to/ortofoto.tif --grid data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp

  # Process a single tile (useful for debugging):
  python run_langsam.py --ortho /path/to/ortofoto.tif --tile-id 31TCF4158

  # Force reprocessing even if output already exists:
  python run_langsam.py --ortho /path/to/ortofoto.tif --overwrite

Dependencies
------------
  torch torchvision (install separately with CUDA support)
  lang-sam (pip install git+https://github.com/luca-medeiros/lang-segment-anything.git)
  rasterio, geopandas, shapely, numpy, Pillow, click, pyyaml, tqdm, affine
"""

from __future__ import annotations

import json
import sys
import tempfile
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
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.ops import unary_union
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
config = yaml.safe_load(open(CONFIG_PATH))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / config["raw_masks_dir"]
RAW_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_GRID = PROJECT_ROOT / "data" / "shp" / "test_quadricules.geojson"
DEFAULT_ORTHO = Path(config["seagate_path"]) / config["ortho_file"]

# ---------------------------------------------------------------------------
# PV text prompts for GroundingDINO
# Each phrase must end with "." — that is the GroundingDINO convention.
# LangSAM runs one predict() call per prompt and we union all results.
#
# NOTE: "rooftop solar." and "solar energy installation." were removed —
# testing on 31TBF6932/31TCF4158 showed these two generic prompts were
# matching entire roof coverings (false positives), not just PV arrays.
# ---------------------------------------------------------------------------
PV_PROMPTS: list[str] = [
    "solar panel.",
    "photovoltaic panel.",
    "PV installation.",
    "solar module.",
    "photovoltaic array.",
]

# ---------------------------------------------------------------------------
# Raster helpers
# ---------------------------------------------------------------------------

def clip_ortho_to_geom(
    ortho_path: Path, geom, geom_crs_epsg: int, min_data_fraction: float = 0.05
) -> Optional[Path]:
    """
    Clip ortho_path to geom (in geom_crs_epsg). Returns path to temp GeoTIFF,
    or None if the geometry does not intersect the raster extent, OR if the
    clipped area is almost entirely nodata (e.g. sea / outside the real
    ortho footprint but still inside its bounding box) — detected via the
    alpha band when present. This avoids wasting inference time on empty
    tiles during a country-wide run.
    Caller must delete the temp file after use.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    with rasterio.open(ortho_path) as src:
        gdf = gpd.GeoDataFrame(geometry=[geom], crs=f"EPSG:{geom_crs_epsg}")
        gdf = gdf.to_crs(src.crs)

        from shapely.geometry import box
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
    Handles 1-band (grayscale→RGB) and n-band rasters.
    """
    with rasterio.open(tif_path) as src:
        n = src.count
        if n >= 3:
            data = src.read([1, 2, 3])   # (3, H, W)
        else:
            band = src.read(1)            # (H, W)
            data = np.stack([band, band, band])
        transform = src.transform
        crs_wkt = src.crs.to_wkt() if src.crs else ""

    rgb = np.moveaxis(data, 0, -1)        # (H, W, 3)
    if rgb.dtype != np.uint8:
        dmax = rgb.max()
        rgb = ((rgb / dmax) * 255).astype(np.uint8) if dmax > 0 else rgb.astype(np.uint8)

    return Image.fromarray(rgb), transform, crs_wkt


def pixel_masks_to_polygons(masks: np.ndarray, transform: Affine) -> list:
    """
    Union N binary masks (N×H×W) and vectorise pixel blobs to
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
# Donut geometry
# ---------------------------------------------------------------------------

def build_donut(tile_geom, overlap_fraction: float = 0.25):
    """
    Return the overlap ring (donut) around a tile.
    For a 1 km tile, 25 % overlap = 250 m square buffer on each side.
    """
    buffer_m = 1000 * overlap_fraction
    outer = tile_geom.buffer(buffer_m, join_style=2)  # join_style=2 → mitre/square
    return outer.difference(tile_geom)


# ---------------------------------------------------------------------------
# LangSAM inference
# ---------------------------------------------------------------------------

_MODEL_CACHE = {}


def get_model() -> "LangSAM":  # noqa: F821
    """Load LangSAM once and reuse across tiles (avoids reloading weights per tile)."""
    if "model" not in _MODEL_CACHE:
        from lang_sam import LangSAM  # noqa: lazy import
        _MODEL_CACHE["model"] = LangSAM()
    return _MODEL_CACHE["model"]


def run_langsam(
    pil_image: Image.Image,
    transform: Affine,
    box_threshold: float = 0.35,
    text_threshold: float = 0.3,
) -> list:
    """
    Run LangSAM with every PV prompt. Returns a flat list of Shapely polygons
    georeferenced in the same CRS as the input raster tile.
    """
    model = get_model()
    all_polygons: list = []

    for prompt in PV_PROMPTS:
        results = model.predict(
            [pil_image], [prompt],
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        # results: list[dict] — one dict per image in the batch
        # dict keys: masks (N×H×W tensor or array), boxes, scores, labels
        for result in results:
            masks = result.get("masks")
            if masks is None or len(masks) == 0:
                continue
            masks_np = np.asarray(masks, dtype=bool)  # (N, H, W)
            polys = pixel_masks_to_polygons(masks_np, transform)
            all_polygons.extend(polys)

    return all_polygons


# ---------------------------------------------------------------------------
# GeoJSON output
# ---------------------------------------------------------------------------

def to_geojson(polygons: list, tile_id: str, region: str) -> dict:
    """Merge overlapping polygons and build a GeoJSON FeatureCollection."""
    if not polygons:
        return {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
            "features": [],
        }

    merged = unary_union(polygons)
    geoms = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(g),
                "properties": {
                    "tile_id": tile_id,
                    "region": region,
                    "mask_id": i,
                    "crs": "EPSG:25831",
                },
            }
            for i, g in enumerate(geoms)
            if not g.is_empty
        ],
    }


def save_geojson(data: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def expected_outputs(tile_id: str, process_donut: bool) -> list[Path]:
    """Paths that must exist for this tile to be considered already done."""
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
) -> dict:
    """
    Run the full pipeline for one tile. Returns a summary dict.
    """
    summary = {"tile_id": tile_id, "core": 0, "donut": 0, "skipped": False, "already_done": False}

    if not overwrite and tile_already_done(tile_id, process_donut):
        summary["already_done"] = True
        return summary

    # --- Core tile ---
    tmp = clip_ortho_to_geom(ortho_path, tile_geom, crs_epsg)
    if tmp is None:
        summary["skipped"] = True
        return summary

    try:
        pil_img, transform, _ = tif_to_pil(tmp)
        polys = run_langsam(pil_img, transform, box_threshold, text_threshold)
        summary["core"] = len(polys)
        save_geojson(
            to_geojson(polys, tile_id, "core"),
            RAW_DIR / f"{tile_id}_core.geojson",
        )
    finally:
        tmp.unlink(missing_ok=True)

    # --- Donut (25 % overlap ring) ---
    if process_donut:
        donut_geom = build_donut(tile_geom, overlap_fraction)
        tmp_d = clip_ortho_to_geom(ortho_path, donut_geom, crs_epsg)
        if tmp_d is not None:
            try:
                pil_d, transform_d, _ = tif_to_pil(tmp_d)
                polys_d = run_langsam(pil_d, transform_d, box_threshold, text_threshold)
                summary["donut"] = len(polys_d)
                save_geojson(
                    to_geojson(polys_d, tile_id, "donut"),
                    RAW_DIR / f"{tile_id}_donut.geojson",
                )
            finally:
                tmp_d.unlink(missing_ok=True)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--ortho", "ortho_path", default=str(DEFAULT_ORTHO), show_default=True, type=click.Path(exists=True),
    help="Path to ortho mosaic GeoTIFF (or GPKG raster). "
         "This is the large ICGC ortofoto file on the Seagate drive.",
)
@click.option(
    "--grid", "grid_path", default=str(DEFAULT_GRID), show_default=True,
    type=click.Path(exists=True),
    help="GeoJSON or GPKG/SHP with UTM 1km quadricule polygons. "
         "Defaults to data/shp/test_quadricules.geojson (2 test tiles). "
         "For a full run, supply the complete ICGC UTM 1km grid, e.g. "
         "data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp",
)
@click.option(
    "--tile-id", default=None,
    help="Process only this tile ID (e.g. 31TCF4158). "
         "If omitted, all tiles in --grid that intersect --ortho are processed.",
)
@click.option(
    "--process-donut", is_flag=True, default=False,
    help="Also run LangSAM on the 25% overlap donut ring for each tile.",
)
@click.option(
    "--overlap-fraction", default=None, type=float,
    help="Overlap fraction for donut (default: from config.yaml, currently "
         f"{config.get('overlap_fraction', 0.25)}).",
)
@click.option(
    "--box-threshold", default=0.35, type=float, show_default=True,
    help="GroundingDINO box confidence threshold (higher = fewer, stricter detections).",
)
@click.option(
    "--text-threshold", default=0.3, type=float, show_default=True,
    help="GroundingDINO text confidence threshold (higher = fewer, stricter detections).",
)
@click.option(
    "--overwrite", is_flag=True, default=False,
    help="Reprocess tiles even if their output GeoJSON(s) already exist. "
         "Without this flag, already-processed tiles are skipped (resume mode).",
)
def main(ortho_path, grid_path, tile_id, process_donut, overlap_fraction,
         box_threshold, text_threshold, overwrite):
    """
    Automated PV detection on ortho tiles using LangSAM.

    For each UTM 1km quadricule that intersects the ortho mosaic, this script
    clips the ortho, runs LangSAM with multiple PV text prompts, and saves
    georeferenced GeoJSON masks to data/masks/raw/.

    Safe to interrupt (Ctrl+C, container stop, reboot) and resume later:
    tiles whose output already exists are skipped automatically unless
    --overwrite is passed.
    """
    ortho = Path(ortho_path)
    grid = Path(grid_path)
    overlap = overlap_fraction or config.get("overlap_fraction", 0.25)

    # Load grid
    gdf = gpd.read_file(grid)
    crs_epsg = gdf.crs.to_epsg()
    tile_id_col = "COORD_1K"  # column name in ICGC UTM grid

    if tile_id_col not in gdf.columns:
        click.echo(f"ERROR: column '{tile_id_col}' not found in grid. "
                   f"Available columns: {gdf.columns.tolist()}")
        sys.exit(1)

    if tile_id:
        gdf = gdf[gdf[tile_id_col] == tile_id]
        if gdf.empty:
            click.echo(f"ERROR: tile '{tile_id}' not found in grid.")
            sys.exit(1)

    click.echo(f"\nOrtho       : {ortho}")
    click.echo(f"Grid        : {grid}  ({len(gdf)} tile(s))")
    click.echo(f"Output dir  : {RAW_DIR}")
    click.echo(f"Prompts     : {len(PV_PROMPTS)} — {PV_PROMPTS}")
    click.echo(f"Donut       : {'yes' if process_donut else 'no'}")
    click.echo(f"Overlap     : {overlap*100:.0f}%")
    click.echo(f"Box thresh  : {box_threshold}")
    click.echo(f"Text thresh : {text_threshold}")
    click.echo(f"Overwrite   : {'yes' if overwrite else 'no (resume mode)'}\n")

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
        s = process_tile(t_id, t_geom, crs_epsg, ortho, process_donut, overlap,
                          box_threshold, text_threshold, overwrite)
        summaries.append(s)
        
        core_file = RAW_DIR / f"{t_id}_core.geojson"
        if core_file.exists():
            with open(core_file) as f:
                core_geojson = json.load(f)
            all_features.extend(core_geojson["features"])

        # Afegir també el donut si existeix
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
    merged_geojson = {
    "type": "FeatureCollection",
    "crs": {
        "type": "name",
        "properties": {
            "name": "urn:ogc:def:crs:EPSG::25831"
        }
    },
    "features": all_features,
    }

    merged_path = RAW_DIR / "langsam_merged.geojson"

    with open(merged_path, "w") as f:
        json.dump(merged_geojson, f, indent=2)

    click.echo(f"Merged GeoJSON: {merged_path}")
    click.echo(f"Output: {RAW_DIR}\n")


if __name__ == "__main__":
    main()
