#!/usr/bin/env python3
"""
tarraco_rois CLI — Main entry point for PV detection pipeline.

Usage:
    python -m tarraco_rois run --module langsam
    python -m tarraco_rois modules
    python -m tarraco_rois config --check
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config() -> dict:
    """Load global configuration."""
    if not CONFIG_PATH.exists():
        click.echo(f"ERROR: Config file not found: {CONFIG_PATH}")
        sys.exit(1)
    return yaml.safe_load(open(CONFIG_PATH))


def get_modules_dir() -> Path:
    """Get modules directory path."""
    return PROJECT_ROOT / "modules"


def list_modules() -> list[str]:
    """List available modules."""
    modules_dir = get_modules_dir()
    if not modules_dir.exists():
        return []
    return [
        d.name
        for d in modules_dir.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    ]


def load_module_config(module_name: str) -> dict:
    """Load module-specific configuration."""
    module_config_path = get_modules_dir() / module_name / "config.yaml"
    if module_config_path.exists():
        return yaml.safe_load(open(module_config_path))
    return {}


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """tarraco-rois — PV detection from aerial imagery."""
    pass


@cli.command()
@click.option("--module", "-m", required=True, help="Module(s) to run, comma-separated")
@click.option("--ortho", type=click.Path(exists=True), help="Orthophoto path (overrides config)")
@click.option("--tiles", type=click.Path(exists=True), help="Tiles directory or GeoJSON grid")
@click.option("--tile-id", help="Process single tile ID")
@click.option("--overwrite", is_flag=True, help="Overwrite existing outputs")
@click.option("--params", multiple=True, help="Module params as key=value")
def run(module, ortho, tiles, tile_id, overwrite, params):
    """Run PV detection module(s)."""
    config = load_config()
    project_root = Path(config.get("project_root", str(PROJECT_ROOT)))

    # Resolve ortho path
    ortho_path = Path(ortho) if ortho else project_root / config.get("ortho_path", "sandbox/data/ortho/")
    if not ortho_path.exists():
        click.echo(f"ERROR: Ortho path not found: {ortho_path}")
        sys.exit(1)

    # Parse extra params
    extra_params = {}
    for p in params:
        if "=" in p:
            k, v = p.split("=", 1)
            extra_params[k.strip()] = v.strip()

    # Process each module
    modules = [m.strip() for m in module.split(",")]
    for mod_name in modules:
        click.echo(f"\n{'='*60}")
        click.echo(f"Running module: {mod_name}")
        click.echo(f"{'='*60}")

        mod_config = load_module_config(mod_name)
        mod_config.update(extra_params)

        # Import and run module
        try:
            mod_path = get_modules_dir() / mod_name
            sys.path.insert(0, str(mod_path.parent))
            mod = __import__(mod_name)

            if hasattr(mod, "run_pipeline"):
                mod.run_pipeline(
                    ortho_path=ortho_path,
                    config=mod_config,
                    output_dir=project_root / config.get("output_path", "dist/masks") / mod_name,
                    tiles_path=Path(tiles) if tiles else None,
                    tile_id=tile_id,
                    overwrite=overwrite,
                )
            else:
                click.echo(f"WARNING: Module {mod_name} has no run_pipeline function")
        except ImportError as e:
            click.echo(f"ERROR: Could not import module {mod_name}: {e}")
            sys.exit(1)
        except Exception as e:
            click.echo(f"ERROR: Module {mod_name} failed: {e}")
            sys.exit(1)


@cli.command()
def modules():
    """List available modules."""
    mods = list_modules()
    if not mods:
        click.echo("No modules found in modules/")
        return

    click.echo("Available modules:")
    for mod in mods:
        config = load_module_config(mod)
        desc = config.get("description", "No description")
        click.echo(f"  {mod:<20} — {desc}")


@cli.command()
@click.option("--check", is_flag=True, help="Validate configuration")
def config(check):
    """Show or validate configuration."""
    cfg = load_config()

    if check:
        click.echo("Checking configuration...")
        errors = []

        # Check required keys
        required = ["project_name", "ortho_path", "output_path"]
        for key in required:
            if key not in cfg:
                errors.append(f"Missing required key: {key}")

        # Check ortho path exists
        ortho_path = PROJECT_ROOT / cfg.get("ortho_path", "")
        if not ortho_path.exists():
            errors.append(f"Ortho path does not exist: {ortho_path}")

        # Check modules
        mods = list_modules()
        if not mods:
            errors.append("No modules found")

        if errors:
            click.echo("Configuration errors:")
            for e in errors:
                click.echo(f"  - {e}")
            sys.exit(1)
        else:
            click.echo("Configuration OK")
            click.echo(f"  Project: {cfg.get('project_name')}")
            click.echo(f"  Modules: {', '.join(mods)}")
    else:
        click.echo(yaml.dump(cfg, default_flow_style=False))


@cli.command()
@click.option("--masks", type=click.Path(exists=True, path_type=Path), required=True,
              help="Directory with detected mask GeoJSON files.")
@click.option("--reference", type=click.Path(exists=True, path_type=Path), required=True,
              help="Reference point dataset (GeoPackage/Shapefile with prosumer locations).")
@click.option("--buffer", type=float, default=50.0, show_default=True,
              help="Buffer distance in meters around masks.")
@click.option("--snap-tolerance", type=float, default=10.0, show_default=True,
              help="Snap tolerance for point-in-mask test (meters).")
@click.option("--tile-id", default=None, help="Process only masks for this tile ID.")
@click.option("--output", type=click.Path(path_type=Path), default=None,
              help="Output directory for analysis results.")
def analyze(masks, reference, buffer, snap_tolerance, tile_id, output):
    """Analyze proximity between detected masks and reference points."""
    sys.path.insert(0, str(PROJECT_ROOT / "modules" / "langsam"))
    from proximity import (
        load_masks,
        load_reference_points,
        analyze_proximity,
        generate_report,
        export_annotated_masks,
    )

    output_dir = output or PROJECT_ROOT / "dist" / "analysis"

    click.echo(f"Loading masks from: {masks}")
    masks_gdf = load_masks(masks, tile_id)
    if masks_gdf.empty:
        click.echo("No masks found.")
        return
    click.echo(f"  Loaded {len(masks_gdf)} masks")

    click.echo(f"Loading reference from: {reference}")
    ref_gdf = load_reference_points(reference)
    click.echo(f"  Loaded {len(ref_gdf)} reference points")

    click.echo(f"\nRunning proximity analysis (buffer={buffer}m, snap={snap_tolerance}m)...")
    results = analyze_proximity(masks_gdf, ref_gdf, buffer, snap_tolerance)

    generate_report(results, output_dir)
    export_annotated_masks(masks_gdf, ref_gdf, output_dir, buffer)


@cli.command()
@click.argument("lat", type=float)
@click.argument("lon", type=float)
@click.option("--size", type=int, default=1000, show_default=True, help="Area size in meters (square)")
@click.option("--format", "fmt", type=click.Choice(["geotiff", "jpeg", "png"]), default="geotiff",
              show_default=True, help="Output format")
@click.option("--service", type=click.Choice(["icgc", "ign"]), default=None,
              help="WMS service (auto-detect based on location)")
@click.option("--layer", default=None, help="WMS layer name (uses service default)")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None,
              help="Output directory (default: sandbox/data/ortho/)")
@click.option("--output-name", default=None, help="Output filename without extension")
def fetch(lat, lon, size, fmt, service, layer, output_dir, output_name):
    """Download orthophoto from WMS service (ICGC/IGN) by coordinates."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from fetch_ortho import fetch_ortho, validate_download

    out = fetch_ortho(
        center_lat=lat,
        center_lon=lon,
        size_m=size,
        output_format=fmt,
        service_key=service,
        layer=layer,
        output_dir=output_dir,
        output_name=output_name,
    )
    val = validate_download(out)
    if val["valid"]:
        click.echo(f"\nOK: {val['width']}x{val['height']} px, {val['format']}, {val['size_kb']:.0f} KB")
    else:
        click.echo(f"\nWARNING: File may be invalid: {val}")


if __name__ == "__main__":
    cli()
