#!/usr/bin/env python3
"""
smoke_test.py — Quick validation of project structure and pipeline integrity.

Run with: python smoke_test.py
No external dependencies required (only stdlib).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

errors = []
warnings = []


def check(description: str, condition: bool, error_msg: str = ""):
    """Check a condition and record result."""
    if condition:
        print(f"  [OK] {description}")
    else:
        print(f"  [FAIL] {description}")
        if error_msg:
            errors.append(error_msg)
        else:
            errors.append(description)


def warn(description: str, msg: str):
    """Record a warning."""
    print(f"  [WARN] {description}")
    warnings.append(msg)


def test_directory_structure():
    """Test that required directories exist."""
    print("\n1. Testing directory structure...")

    required_dirs = [
        ("modules", "Modules directory"),
        ("modules/langsam", "LangSAM module"),
        ("modules/langsam/prompts", "LangSAM prompts dir"),
        ("res", "Resources directory"),
        ("res/data", "Resources data dir"),
        ("res/data/gpkg", "GeoPackages dir"),
        ("res/data/shp", "Shapefiles dir"),
        ("res/data/tiles", "Tiles dir"),
        ("res/qgis", "QGIS dir"),
        ("sandbox", "Sandbox directory"),
        ("sandbox/data", "Sandbox data dir"),
        ("sandbox/data/ortho", "Sandbox ortho dir"),
        ("sandbox/models", "Sandbox models dir"),
        ("sandbox/models/langsam", "Sandbox LangSAM models dir"),
        ("sandbox/tiles", "Sandbox tiles dir"),
        ("sandbox/temp", "Sandbox temp dir"),
        ("dist", "Distribution directory"),
        ("dist/masks", "Dist masks dir"),
        ("dist/masks/merged", "Dist merged dir"),
        ("tests", "Tests directory"),
        ("tests/config", "Config tests dir"),
        ("tests/langsam", "LangSAM tests dir"),
        ("scripts", "Scripts directory"),
    ]

    for dir_path, description in required_dirs:
        full_path = PROJECT_ROOT / dir_path
        check(description, full_path.exists(), f"Missing directory: {dir_path}")


def test_required_files():
    """Test that required files exist."""
    print("\n2. Testing required files...")

    required_files = [
        ("config.yaml", "Global config"),
        ("README.md", "README"),
        ("PLAN.md", "Plan document"),
        ("AGENTS.md", "Agents document"),
        ("Dockerfile", "Dockerfile"),
        ("docker-compose.yml", "Docker compose"),
        ("requirements.txt", "Requirements"),
        ("scripts/cli.py", "CLI entry point"),
        ("__init__.py", "Package init"),
        ("tarraco_rois.py", "Main module"),
        ("modules/__init__.py", "Modules init"),
        ("modules/langsam/__init__.py", "LangSAM init"),
        ("modules/langsam/langsam.py", "LangSAM implementation"),
        ("modules/langsam/config.yaml", "LangSAM config"),
        ("modules/langsam/prompts/pv_prompts.txt", "LangSAM prompts"),
        ("modules/langsam/evaluate.py", "LangSAM evaluate"),
        ("modules/langsam/summarize.py", "LangSAM summarize"),
        ("modules/langsam/tiles.py", "LangSAM tiles"),
        ("tests/__init__.py", "Tests init"),
        ("tests/config/test_config.py", "Config tests"),
        ("tests/langsam/test_langsam.py", "LangSAM tests"),
        (".gitignore", "Git ignore"),
    ]

    for file_path, description in required_files:
        full_path = PROJECT_ROOT / file_path
        check(description, full_path.exists(), f"Missing file: {file_path}")


def test_module_langsam_files():
    """Test LangSAM module has all required content."""
    print("\n3. Testing LangSAM module content...")

    langsam_dir = PROJECT_ROOT / "modules" / "langsam"

    # Check __init__.py has content
    init_file = langsam_dir / "__init__.py"
    if init_file.exists():
        content = init_file.read_text()
        check("__init__.py imports run_pipeline", "run_pipeline" in content)
    else:
        check("__init__.py exists", False, "Missing __init__.py")

    # Check config.yaml has required keys
    config_file = langsam_dir / "config.yaml"
    if config_file.exists():
        content = config_file.read_text()
        check("Config has module name", "module: langsam" in content)
        check("Config has box_threshold", "box_threshold" in content)
        check("Config has text_threshold", "text_threshold" in content)
        check("Config has prompts", "prompts:" in content)
    else:
        check("config.yaml exists", False, "Missing config.yaml")

    # Check prompts file has content
    prompts_file = langsam_dir / "prompts" / "pv_prompts.txt"
    if prompts_file.exists():
        content = prompts_file.read_text()
        prompts = [l.strip() for l in content.strip().split("\n") if l.strip()]
        check("Prompts file has entries", len(prompts) > 0)
        check("Prompts end with '.'", all(p.endswith(".") for p in prompts))
    else:
        check("pv_prompts.txt exists", False, "Missing prompts file")

    # Check langsam.py has key functions
    langsam_file = langsam_dir / "langsam.py"
    if langsam_file.exists():
        content = langsam_file.read_text()
        check("langsam.py has run_langsam function", "def run_langsam" in content)
        check("langsam.py has clip_ortho_to_geom", "def clip_ortho_to_geom" in content)
        check("langsam.py has filter_polygons", "def filter_polygons" in content)
        check("langsam.py has merge_nearby_polygons", "def merge_nearby_polygons" in content)
        check("langsam.py has to_geojson", "def to_geojson" in content)
    else:
        check("langsam.py exists", False, "Missing langsam.py")


def test_config_yaml():
    """Test global config.yaml."""
    print("\n4. Testing global config...")

    config_file = PROJECT_ROOT / "config.yaml"
    if config_file.exists():
        content = config_file.read_text()
        check("Config has project_name", "project_name:" in content)
        check("Config has ortho_path", "ortho_path:" in content)
        check("Config has output_path", "output_path:" in content)
    else:
        check("config.yaml exists", False, "Missing config.yaml")


def test_cli():
    """Test CLI entry point."""
    print("\n5. Testing CLI...")

    cli_file = PROJECT_ROOT / "scripts" / "cli.py"
    if cli_file.exists():
        content = cli_file.read_text()
        check("CLI has click imports", "import click" in content)
        check("CLI has main group", "@click.group()" in content)
        check("CLI has run command", "def run" in content)
        check("CLI has modules command", "def modules" in content)
        check("CLI has config command", "def config" in content)
    else:
        check("cli.py exists", False, "Missing cli.py")


def test_paths_not_hardcoded():
    """Test that paths are not hardcoded to old locations."""
    print("\n6. Testing for hardcoded paths...")

    old_paths = [
        "data/masks/raw",
        "data/masks/validated",
        "data/gpkg/",
        "data/shp/",
        "seagate_path",
        "/media/llusaga",
    ]

    files_to_check = [
        "modules/langsam/langsam.py",
        "modules/langsam/evaluate.py",
        "modules/langsam/summarize.py",
        "modules/langsam/tiles.py",
        "scripts/cli.py",
    ]

    for file_path in files_to_check:
        full_path = PROJECT_ROOT / file_path
        if full_path.exists():
            content = full_path.read_text()
            for old_path in old_paths:
                if old_path in content:
                    warn(f"{file_path} contains '{old_path}'", f"Potential hardcoded path: {old_path}")


def test_res_data_copied():
    """Test that res/data has the expected files."""
    print("\n7. Testing res/data content...")

    gpkg_dir = PROJECT_ROOT / "res" / "data" / "gpkg"
    if gpkg_dir.exists():
        gpkg_files = list(gpkg_dir.glob("*.gpkg"))
        check("GeoPackages copied", len(gpkg_files) > 0, "No .gpkg files in res/data/gpkg/")
    else:
        check("gpkg dir exists", False, "Missing res/data/gpkg/")

    shp_dir = PROJECT_ROOT / "res" / "data" / "shp"
    if shp_dir.exists():
        shp_files = list(shp_dir.rglob("*.shp"))
        check("Shapefiles copied", len(shp_files) > 0, "No .shp files in res/data/shp/")
    else:
        check("shp dir exists", False, "Missing res/data/shp/")


def main():
    """Run all smoke tests."""
    print("=" * 60)
    print("tarraco-rois Smoke Test")
    print("=" * 60)

    test_directory_structure()
    test_required_files()
    test_module_langsam_files()
    test_config_yaml()
    test_cli()
    test_paths_not_hardcoded()
    test_res_data_copied()

    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("PASSED: All checks OK")
        if warnings:
            print(f"\nWarnings ({len(warnings)}):")
            for w in warnings:
                print(f"  - {w}")
        sys.exit(0)


if __name__ == "__main__":
    main()
