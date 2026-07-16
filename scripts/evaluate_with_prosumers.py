"""Compare raw GeoJSON masks with the prosumers point dataset to help manual review.

Outputs a CSV with counts of prosumers within each mask and suggested review flags.
"""
import geopandas as gpd
from shapely.geometry import shape
from pathlib import Path
import json
import click

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config.yaml'
import yaml
config = yaml.safe_load(open(CONFIG_PATH))

RAW_DIR = Path(config['raw_masks_dir'])
PROSUMERS = Path(config['prosumers_gpkg'])


@click.command()
@click.option('--tile-id', required=True, help='Tile ID to evaluate')
def main(tile_id):
    mask_file = RAW_DIR / f"{tile_id}.geojson"
    if not mask_file.exists():
        print(f"Mask not found: {mask_file}")
        return

    masks = gpd.read_file(mask_file)
    pros = gpd.read_file(PROSUMERS)

    # ensure same CRS
    pros = pros.to_crs(masks.crs)

    # count prosumers inside each mask (or 0 if no masks)
    rows = []
    for idx, row in masks.iterrows():
        geom = row.geometry
        count = pros.within(geom).sum()
        rows.append({'mask_id': idx, 'prosumers_inside': int(count)})

    if not rows:
        print('No masks found - nothing to evaluate')
        return

    import pandas as pd
    out_csv = RAW_DIR / f"{tile_id}_review.csv"
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    print(f"Wrote review CSV to {out_csv}")


if __name__ == '__main__':
    main()
