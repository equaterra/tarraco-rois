#!/usr/bin/env python3
"""
make_1km_tiles.py — Utility to inspect and filter UTM 1km grid tiles.

Usage
-----
  # List all tiles in the grid:
  python tiles.py --grid res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp

  # List tiles that have already been processed:
  python tiles.py --grid res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp --status processed

  # List tiles that still need processing:
  python tiles.py --grid res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp --status pending

  # Filter tiles by bounding box (west, south, east, north in UTM):
  python tiles.py --grid res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp --bbox 400000 4500000 500000 4600000

  # Export tile IDs to a text file:
  python tiles.py --grid res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp --export tiles.txt
"""

from pathlib import Path

import click
import geopandas as gpd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
config = yaml.safe_load(open(CONFIG_PATH))

RAW_DIR = PROJECT_ROOT / config.get("output_path", "dist/masks") / "langsam"


@click.command()
@click.option(
    "--grid",
    type=click.Path(exists=True),
    required=True,
    help="Path to UTM 1km grid shapefile/GeoJSON/GPKG (e.g. res/data/shp/quadricules-utm-v1r0-2021/quadricules-utm-v1r0-2021.shp).",
)
@click.option(
    "--status",
    type=click.Choice(["all", "processed", "pending"], case_sensitive=False),
    default="all",
    help="Filter by processing status.",
)
@click.option(
    "--bbox",
    type=float,
    nargs=4,
    default=None,
    help="Filter tiles by bounding box: west south east north (in grid CRS units).",
)
@click.option(
    "--export",
    type=click.Path(path_type=Path),
    default=None,
    help="Export tile IDs to a text file (one per line).",
)
def main(grid, status, bbox, export):
    """List and filter UTM 1km grid tiles."""
    gdf = gpd.read_file(grid)
    tile_id_col = "COORD_1K"

    if tile_id_col not in gdf.columns:
        click.echo(f"ERROR: column '{tile_id_col}' not found. Available: {gdf.columns.tolist()}")
        return

    click.echo(f"Grid: {grid}  ({len(gdf)} tiles total)")

    # Filter by bounding box
    if bbox:
        from shapely.geometry import box

        west, south, east, north = bbox
        filter_box = box(west, south, east, north)
        gdf = gdf[gdf.geometry.intersects(filter_box)]
        click.echo(f"After bbox filter: {len(gdf)} tiles")

    # Filter by status
    if status != "all":
        tile_ids = set(gdf[tile_id_col])
        processed = set()
        pending = set()
        for tid in tile_ids:
            core_file = RAW_DIR / f"{tid}_core.geojson"
            if core_file.exists():
                processed.add(tid)
            else:
                pending.add(tid)

        if status == "processed":
            gdf = gdf[gdf[tile_id_col].isin(processed)]
        elif status == "pending":
            gdf = gdf[gdf[tile_id_col].isin(pending)]

        click.echo(f"After status filter ({status}): {len(gdf)} tiles")

    # List tiles
    click.echo("\nTile IDs:")
    ids = gdf[tile_id_col].tolist()
    for tid in ids:
        click.echo(f"  {tid}")

    click.echo(f"\nTotal: {len(ids)} tiles")

    # Export
    if export:
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text("\n".join(ids) + "\n")
        click.echo(f"Exported {len(ids)} tile IDs to {export}")


if __name__ == "__main__":
    main()
