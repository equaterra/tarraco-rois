"""Tests for LangSAM module."""

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"
sys.path.insert(0, str(MODULES_DIR))


def test_langsam_import():
    """Test that langsam module can be imported."""
    import langsam
    assert hasattr(langsam, "run_pipeline")


def test_langsam_config():
    """Test that langsam config is valid."""
    config_path = MODULES_DIR / "langsam" / "config.yaml"
    assert config_path.exists(), "LangSAM config not found"
    config = yaml.safe_load(open(config_path))
    assert config["module"] == "langsam"
    assert "box_threshold" in config
    assert "text_threshold" in config
    assert "prompts" in config


def test_langsam_prompts():
    """Test that prompts are properly formatted."""
    config_path = MODULES_DIR / "langsam" / "config.yaml"
    config = yaml.safe_load(open(config_path))
    prompts = config["prompts"]
    assert isinstance(prompts, list)
    assert len(prompts) > 0
    for prompt in prompts:
        assert prompt.endswith("."), f"Prompt must end with '.': {prompt}"


def test_langsam_files_exist():
    """Test that all required langsam files exist."""
    langsam_dir = MODULES_DIR / "langsam"
    required_files = [
        "__init__.py",
        "langsam.py",
        "config.yaml",
        "prompts/pv_prompts.txt",
    ]
    for f in required_files:
        assert (langsam_dir / f).exists(), f"Missing file: {f}"
