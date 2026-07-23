#!/usr/bin/env python3
"""
summarize_masks.py — Merge all per-tile GeoJSON masks and compute area stats.

Input
-----
  All GeoJSON files in dist/masks/langsam/ matching:
    *_core.geojson   (mandatory)
    *_donut.geojson  (optional, if --include-donut)

Output
------
  Merged GeoPackage (or GeoJSON) with all polygons + area (m²).
  CSV summary per tile: tile_id, polygon_count, total_area_m2.

Usage
-----
  python -m modules.langsam.summarize
  python -m modules.langsam.summarize --include-donut --output dist/masks/merged
"""

import json
from pathlib import Path
import click
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = PROJECT_ROOT / "dist" / "masks" / "langsam"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "masks" / "merged"


@click.command()
@click.option(
    "--raw-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=DEFAULT_RAW_DIR,
    help="Directory containing per-tile *_core.geojson (and optionally *_donut.geojson).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    help="Where to write merged files.",
)
@click.option(
    "--include-donut",
    is_flag=True,
    default=False,
    help="Also include *_donut.geojson files in the merged dataset.",
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(["gpkg", "geojson"], case_sensitive=False),
    default="gpkg",
    help="Output format for the merged geometries.",
)
def main(raw_dir, output_dir, include_donut, out_format):
    """Merge all per-tile masks, compute area, and produce summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all core files
    core_files = sorted(raw_dir.glob("*_core.geojson"))
    if not core_files:
        click.echo(f"No *_core.geojson files found in {raw_dir}")
        return

    click.echo(f"Found {len(core_files)} core tile files.")

    # Prepare list of GeoDataFrames
    gdfs = []

    # --- Core ---
    for f in core_files:
        tile_id = f.stem.replace("_core", "")
        gdf = gpd.read_file(f)
        if gdf.empty:
            continue
        gdf["tile_id"] = tile_id
        gdf["region"] = "core"
        gdfs.append(gdf)

    # --- Donut (optional) ---
    if include_donut:
        donut_files = sorted(raw_dir.glob("*_donut.geojson"))
        click.echo(f"Found {len(donut_files)} donut tile files.")
        for f in donut_files:
            tile_id = f.stem.replace("_donut", "")
            gdf = gpd.read_file(f)
            if gdf.empty:
                continue
            gdf["tile_id"] = tile_id
            gdf["region"] = "donut"
            gdfs.append(gdf)

    if not gdfs:
        click.echo("No valid polygons found.")
        return

    # Merge all
    merged = pd.concat(gdfs, ignore_index=True)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=gdfs[0].crs)

    # Ensure CRS is projected (EPSG:25831) for area calculation
    if merged.crs is None:
        merged.set_crs("EPSG:25831", inplace=True)
    if not merged.crs.is_projected:
        click.echo("CRS is not projected – areas will be in degrees. Reprojecting to EPSG:25831.")
        merged = merged.to_crs("EPSG:25831")

    # Compute area in m²
    merged["area_m2"] = merged.geometry.area

    # Output merged geometries
    out_geo = output_dir / f"merged_masks.{out_format}"
    if out_format == "gpkg":
        merged.to_file(out_geo, driver="GPKG", layer="masks")
    else:  # geojson
        merged.to_file(out_geo, driver="GeoJSON")
    click.echo(f"Written merged geometries to {out_geo}")

    # ---- Summary per tile ----
    summary = merged.groupby("tile_id").agg(
        polygon_count=("geometry", "count"),
        total_area_m2=("area_m2", "sum"),
    ).reset_index()
    summary["total_area_ha"] = summary["total_area_m2"] / 10_000

    # Save summary CSV
    out_csv = output_dir / "summary_per_tile.csv"
    summary.to_csv(out_csv, index=False)
    click.echo(f"Written summary to {out_csv}")

    # ---- Print a quick overview ----
    click.echo("\n=== SUMMARY ===")
    click.echo(f"Total polygons: {len(merged):,}")
    click.echo(f"Total area: {summary['total_area_m2'].sum():,.2f} m²  ({summary['total_area_m2'].sum()/1e6:.2f} km²)")
    click.echo(f"Number of tiles with detections: {len(summary)}")
    if not summary.empty:
        click.echo(f"Average area per tile: {summary['total_area_m2'].mean():.2f} m²")
        click.echo(f"Max area tile: {summary.loc[summary['total_area_m2'].idxmax(), 'tile_id']}  ({summary['total_area_m2'].max():.2f} m²)")
    click.echo("\nTop 5 tiles by area:")
    click.echo(summary.nlargest(5, "total_area_m2").to_string(index=False))


if __name__ == "__main__":
    main()
