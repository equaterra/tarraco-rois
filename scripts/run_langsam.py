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

This script is designed to scale from 2 test tiles to hundreds of tiles
across Catalonia without any manual clipping.

Usage
-----
  # Test with the 2 predefined tiles (looks for ortho automatically):
  python run_langsam.py --ortho /path/to/ortofoto.tif

  # Full run over a custom grid:
  python run_langsam.py --ortho /path/to/ortofoto.tif --grid /path/to/grid.gpkg

  # Include 25% overlap donut rings:
  python run_langsam.py --ortho /path/to/ortofoto.tif --process-donut

  # Process a single tile (useful for debugging):
  python run_langsam.py --ortho /path/to/ortofoto.tif --tile-id 31TCF4158

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

RAW_DIR = Path(config["raw_masks_dir"])
RAW_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_GRID = Path(__file__).resolve().parents[2] / "shp" / "test_quadricules.geojson"
DEFAULT_SEAGATE = Path(config["seagate_path"])

# ---------------------------------------------------------------------------
# PV text prompts for GroundingDINO
# Each phrase must end with "." — that is the GroundingDINO convention.
# LangSAM runs one predict() call per prompt and we union all results.
# ---------------------------------------------------------------------------
PV_PROMPTS: list[str] = [
    "solar panel.",
    "photovoltaic panel.",
    "PV installation.",
    "solar farm.",
    "rooftop solar.",
    "solar module.",
    "photovoltaic array.",
    "solar energy installation.",
]

# ---------------------------------------------------------------------------
# Raster helpers
# ---------------------------------------------------------------------------

def clip_ortho_to_geom(ortho_path: Path, geom, geom_crs_epsg: int) -> Optional[Path]:
    """
    Clip ortho_path to geom (in geom_crs_epsg). Returns path to temp GeoTIFF,
    or None if the geometry does not intersect the raster extent.
    Caller must delete the temp file after use.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    with rasterio.open(ortho_path) as src:
        # Reproject geometry to raster CRS
        gdf = gpd.GeoDataFrame(geometry=[geom], crs=f"EPSG:{geom_crs_epsg}")
        gdf = gdf.to_crs(src.crs)

        # Check intersection before clipping
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

def run_langsam(pil_image: Image.Image, transform: Affine) -> list:
    """
    Run LangSAM with every PV prompt. Returns a flat list of Shapely polygons
    georeferenced in the same CRS as the input raster tile.

    LangSAM is imported lazily so this module can be imported without torch/GPU.
    """
    from lang_sam import LangSAM  # noqa: lazy import

    model = LangSAM()
    all_polygons: list = []

    for prompt in PV_PROMPTS:
        results = model.predict([pil_image], [prompt])
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
        return {"type": "FeatureCollection", "features": []}

    merged = unary_union(polygons)
    geoms = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]

    return {
        "type": "FeatureCollection",
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
) -> dict:
    """
    Run the full pipeline for one tile. Returns a summary dict.
    """
    summary = {"tile_id": tile_id, "core": 0, "donut": 0, "skipped": False}

    # --- Core tile ---
    tmp = clip_ortho_to_geom(ortho_path, tile_geom, crs_epsg)
    if tmp is None:
        summary["skipped"] = True
        return summary

    try:
        pil_img, transform, _ = tif_to_pil(tmp)
        polys = run_langsam(pil_img, transform)
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
                polys_d = run_langsam(pil_d, transform_d)
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
    "--ortho", "ortho_path", required=True, type=click.Path(exists=True),
    help="Path to ortho mosaic GeoTIFF (or GPKG raster). "
         "This is the large ICGC ortofoto file on the Seagate drive.",
)
@click.option(
    "--grid", "grid_path", default=str(DEFAULT_GRID), show_default=True,
    type=click.Path(exists=True),
    help="GeoJSON or GPKG with UTM 1km quadricule polygons. "
         "Defaults to shp/test_quadricules.geojson (2 test tiles). "
         "For a full run, supply the complete ICGC UTM 1km grid.",
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
def main(ortho_path, grid_path, tile_id, process_donut, overlap_fraction):
    """
    Automated PV detection on ortho tiles using LangSAM.

    For each UTM 1km quadricule that intersects the ortho mosaic, this script
    clips the ortho, runs LangSAM with multiple PV text prompts, and saves
    georeferenced GeoJSON masks to data/masks/raw/.
    """
    ortho = Path(ortho_path)
    grid = Path(grid_path)
    overlap = overlap_fraction or config.get("overlap_fraction", 0.25)

    # Load grid
    gdf = gpd.read_file(grid)
    crs_epsg = gdf.crs.to_epsg()
    tile_id_col = "COORD_1K"  # column name in ICGC UTM grid

    if tile_id:
        gdf = gdf[gdf[tile_id_col] == tile_id]
        if gdf.empty:
            click.echo(f"ERROR: tile '{tile_id}' not found in grid. "
                       f"Available: {gpd.read_file(grid)[tile_id_col].tolist()}")
            sys.exit(1)

    click.echo(f"\nOrtho       : {ortho}")
    click.echo(f"Grid        : {grid}  ({len(gdf)} tile(s))")
    click.echo(f"Output dir  : {RAW_DIR}")
    click.echo(f"Prompts     : {len(PV_PROMPTS)}")
    click.echo(f"Donut       : {'yes' if process_donut else 'no'}")
    click.echo(f"Overlap     : {overlap*100:.0f}%\n")

    summaries = []
    for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Tiles"):
        t_id = row[tile_id_col]
        t_geom = row.geometry
        click.echo(f"\n--- {t_id} ---")
        s = process_tile(t_id, t_geom, crs_epsg, ortho, process_donut, overlap)
        summaries.append(s)
        status = "SKIPPED (no intersection)" if s["skipped"] else (
            f"core={s['core']} polys  donut={s['donut']} polys"
        )
        click.echo(f"    {status}")

    # Summary table
    click.echo("\n" + "=" * 55)
    click.echo(f"{'TILE':<20} {'CORE':>6} {'DONUT':>6} {'STATUS':>10}")
    click.echo("-" * 55)
    for s in summaries:
        status = "SKIPPED" if s["skipped"] else "OK"
        click.echo(f"{s['tile_id']:<20} {s['core']:>6} {s['donut']:>6} {status:>10}")
    click.echo("=" * 55)
    click.echo(f"Done. Output: {RAW_DIR}\n")


if __name__ == "__main__":
    main()
