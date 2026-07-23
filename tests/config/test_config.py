"""Tests for configuration."""

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_config_exists():
    """Test that config.yaml exists."""
    config_path = PROJECT_ROOT / "config.yaml"
    assert config_path.exists(), f"Config not found: {config_path}"


def test_config_valid():
    """Test that config.yaml is valid YAML."""
    config_path = PROJECT_ROOT / "config.yaml"
    config = yaml.safe_load(open(config_path))
    assert isinstance(config, dict), "Config should be a dictionary"


def test_config_required_keys():
    """Test that required keys are present in config."""
    config_path = PROJECT_ROOT / "config.yaml"
    config = yaml.safe_load(open(config_path))
    required = ["project_name", "ortho_path", "output_path"]
    for key in required:
        assert key in config, f"Missing required key: {key}"


def test_modules_dir_exists():
    """Test that modules directory exists."""
    modules_dir = PROJECT_ROOT / "modules"
    assert modules_dir.exists(), f"Modules dir not found: {modules_dir}"


def test_langsam_module_exists():
    """Test that langsam module exists."""
    langsam_dir = PROJECT_ROOT / "modules" / "langsam"
    assert langsam_dir.exists(), f"LangSAM module not found: {langsam_dir}"
    assert (langsam_dir / "__init__.py").exists(), "LangSAM __init__.py not found"
    assert (langsam_dir / "langsam.py").exists(), "LangSAM langsam.py not found"
    assert (langsam_dir / "config.yaml").exists(), "LangSAM config.yaml not found"


def test_res_dir_exists():
    """Test that res directory exists with subdirectories."""
    res_dir = PROJECT_ROOT / "res"
    assert res_dir.exists(), f"Res dir not found: {res_dir}"
    assert (res_dir / "data").exists(), "Res/data not found"
    assert (res_dir / "qgis").exists(), "Res/qgis not found"


def test_sandbox_dir_exists():
    """Test that sandbox directory exists."""
    sandbox_dir = PROJECT_ROOT / "sandbox"
    assert sandbox_dir.exists(), f"Sandbox dir not found: {sandbox_dir}"
    assert (sandbox_dir / "data").exists(), "Sandbox/data not found"
    assert (sandbox_dir / "models").exists(), "Sandbox/models not found"


def test_dist_dir_exists():
    """Test that dist directory exists."""
    dist_dir = PROJECT_ROOT / "dist"
    assert dist_dir.exists(), f"Dist dir not found: {dist_dir}"
    assert (dist_dir / "masks").exists(), "Dist/masks not found"
