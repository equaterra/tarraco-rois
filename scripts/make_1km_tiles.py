"""Generate 1km UTM grid tile footprints for the study area and extract tile IDs.

This script expects you to provide the bounding geometry or a list of tile IDs.
For the initial test we will handle two tile IDs provided in config.yaml.
"""
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config.yaml'
config = yaml.safe_load(open(CONFIG_PATH))

if __name__ == '__main__':
    print('Test tiles (from config):')
    for t in config.get('test_tiles', []):
        print('-', t)
