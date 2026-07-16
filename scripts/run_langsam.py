"""Run LangSAM / Segment-Anything style pipeline to create GeoJSON masks for tiles.

This is a template script. Replace the `call_langsam_on_image` implementation with the
actual code/integration for the LangSeg/Segment Anything approach you choose.
"""
import os
import sys
import json
from pathlib import Path
import click
import geopandas as gpd
from shapely.geometry import shape

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config.yaml'

import yaml
config = yaml.safe_load(open(CONFIG_PATH))

RAW_DIR = Path(config['raw_masks_dir'])
RAW_DIR.mkdir(parents=True, exist_ok=True)


def call_langsam_on_image(image_path, tile_id):
    """Placeholder: call your LangSAM model here and return GeoJSON FeatureCollection as dict."""
    # For now we return an empty FeatureCollection
    return {"type": "FeatureCollection", "features": []}


@click.command()
@click.option('--tile-id', required=True, help='Tile ID (UTM 1km) to process')
@click.option('--image-path', required=False, help='Path to tile image (overrides config seagate path)')
def main(tile_id, image_path):
    # resolve image
    if image_path:
        image = Path(image_path)
    else:
        image = Path(config['seagate_path']) / f"{tile_id}.gpkg"

    if not image.exists():
        print(f"Image not found: {image}")
        sys.exit(1)

    print(f"Processing tile {tile_id} -> {image}")
    result = call_langsam_on_image(image, tile_id)

    out_path = RAW_DIR / f"{tile_id}.geojson"
    with open(out_path, 'w') as f:
        json.dump(result, f)

    print(f"Wrote raw masks to {out_path}")


if __name__ == '__main__':
    main()
