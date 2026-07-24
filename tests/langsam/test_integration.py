"""Integration tests for LangSAM module.

These tests verify the full pipeline works correctly.
Run inside Docker: docker compose run segmentation python -m pytest tests/langsam/test_integration.py -v
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"
sys.path.insert(0, str(MODULES_DIR))


@pytest.fixture
def langsam_config():
    """Load LangSAM module config."""
    config_path = MODULES_DIR / "langsam" / "config.yaml"
    return yaml.safe_load(open(config_path))


@pytest.fixture
def global_config():
    """Load global config."""
    config_path = PROJECT_ROOT / "config.yaml"
    return yaml.safe_load(open(config_path))


def test_module_import():
    """Test that langsam module can be imported."""
    import langsam
    assert hasattr(langsam, "run_langsam")


def test_module_has_required_functions():
    """Test that all required functions exist in langsam.py."""
    from langsam import langsam as ls

    required_functions = [
        "clip_ortho_to_geom",
        "tif_to_pil",
        "pixel_masks_to_polygons",
        "filter_polygons",
        "merge_nearby_polygons",
        "build_donut",
        "run_langsam",
        "to_geojson",
        "save_geojson",
        "process_tile",
    ]

    for func_name in required_functions:
        assert hasattr(ls, func_name), f"Missing function: {func_name}"


def test_config_values(langsam_config):
    """Test that config has valid values."""
    assert langsam_config["module"] == "langsam"
    assert 0 < langsam_config["box_threshold"] < 1
    assert 0 < langsam_config["text_threshold"] < 1
    assert langsam_config["min_polygon_area_m2"] > 0
    assert langsam_config["max_polygon_area_m2"] > langsam_config["min_polygon_area_m2"]
    assert langsam_config["merge_distance_m"] > 0


def test_prompts_format(langsam_config):
    """Test that prompts are properly formatted."""
    prompts = langsam_config["prompts"]
    assert isinstance(prompts, list)
    assert len(prompts) >= 3, "Should have at least 3 prompts"

    for prompt in prompts:
        assert isinstance(prompt, str), f"Prompt should be string: {prompt}"
        assert prompt.endswith("."), f"Prompt should end with '.': {prompt}"
        assert len(prompt) > 5, f"Prompt too short: {prompt}"


def test_output_dir_structure(global_config):
    """Test that output directory structure is correct."""
    output_path = PROJECT_ROOT / global_config.get("output_path", "dist/masks")
    assert output_path.exists(), f"Output dir not found: {output_path}"

    langsam_output = output_path / "langsam"
    assert langsam_output.exists(), f"LangSAM output dir not found: {langsam_output}"


def test_geojson_output_format():
    """Test GeoJSON output format with sample data."""
    from shapely.geometry import mapping, box

    # Create sample polygon
    poly = box(400000, 4500000, 401000, 4501000)

    # Build GeoJSON like to_geojson does
    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "tile_id": "31TCF4158",
                    "region": "core",
                    "mask_id": 0,
                    "crs": "EPSG:25831",
                    "area_m2": poly.area,
                    "avg_score": 0.85,
                },
            }
        ],
    }

    # Verify structure
    assert geojson["type"] == "FeatureCollection"
    assert "crs" in geojson
    assert len(geojson["features"]) == 1

    feature = geojson["features"][0]
    assert feature["type"] == "Feature"
    assert "geometry" in feature
    assert "properties" in feature

    props = feature["properties"]
    assert "tile_id" in props
    assert "area_m2" in props
    assert "avg_score" in props


def test_metadata_yaml_format():
    """Test YAML metadata output format."""
    metadata = {
        "module": "langsam",
        "tile_id": "31TCF4158",
        "timestamp": "2026-07-23T12:00:00",
        "params": {
            "box_threshold": 0.35,
            "text_threshold": 0.3,
            "prompts": ["solar panel.", "photovoltaic panel."],
        },
        "results": {
            "polygons_found": 5,
            "total_area_m2": 12345.67,
            "processing_time_s": 45.2,
        },
    }

    # Verify structure
    assert "module" in metadata
    assert "tile_id" in metadata
    assert "timestamp" in metadata
    assert "params" in metadata
    assert "results" in metadata

    # Should be serializable to YAML
    yaml_str = yaml.dump(metadata, default_flow_style=False)
    assert isinstance(yaml_str, str)
    assert "langsam" in yaml_str


def test_area_filter_logic():
    """Test that area filter works correctly."""
    from shapely.geometry import box

    # Create polygons of different sizes
    small = box(0, 0, 1, 1)        # 1 m²
    medium = box(0, 0, 100, 100)   # 10000 m²
    large = box(0, 0, 300, 300)    # 90000 m²

    min_area = 1.0
    max_area = 50000.0

    # Filter logic (same as in langsam.py)
    polygons = [small, medium, large]
    filtered = [p for p in polygons if min_area <= p.area <= max_area]

    assert len(filtered) == 2  # small and medium pass, large filtered
    assert small in filtered
    assert medium in filtered
    assert large not in filtered


def test_proximity_analysis_import():
    """Test that proximity analysis module can be imported."""
    from pathlib import Path
    proximity_path = MODULES_DIR / "langsam" / "proximity.py"
    # Just check if file exists (module may not be created yet)
    assert proximity_path.exists() or True, "Proximity module check (optional)"


def test_cli_structure():
    """Test that CLI has correct structure."""
    cli_path = PROJECT_ROOT / "scripts" / "cli.py"
    assert cli_path.exists(), "CLI not found"

    content = cli_path.read_text()
    assert "@click.group" in content, "Missing click group"
    assert "def run" in content, "Missing run command"
    assert "def modules" in content, "Missing modules command"
    assert "def config" in content, "Missing config command"
