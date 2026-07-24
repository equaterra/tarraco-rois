"""Proximity analysis module — Compare detected masks with reference geometries.

This module analyzes the proximity between detected PV masks and reference
point datasets (e.g., prosumer installations) to evaluate detection quality.

Usage:
    python -m modules.langsam.proximity --masks dist/masks/langsam/ --reference res/data/gpkg/prosumers.gpkg
    python -m modules.langsam.proximity --masks dist/masks/langsam/ --reference res/data/gpkg/prosumers.gpkg --buffer 50
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click
import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import box, Point

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
config = yaml.safe_load(open(CONFIG_PATH))


def load_reference_points(reference_path: Path, target_crs: str = "EPSG:25831") -> gpd.GeoDataFrame:
    """Load reference point dataset (prosumers)."""
    gdf = gpd.read_file(reference_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:25831")
    return gdf.to_crs(target_crs)


def load_masks(masks_dir: Path, tile_id: Optional[str] = None) -> gpd.GeoDataFrame:
    """Load detected masks from GeoJSON files."""
    geojson_files = sorted(masks_dir.glob("*.geojson"))

    if tile_id:
        geojson_files = [f for f in geojson_files if tile_id in f.stem]

    if not geojson_files:
        return gpd.GeoDataFrame()

    gdfs = []
    for f in geojson_files:
        gdf = gpd.read_file(f)
        if not gdf.empty:
            gdf["source_file"] = f.name
            gdfs.append(gdf)

    if not gdfs:
        return gpd.GeoDataFrame()

    return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), geometry="geometry")


def analyze_proximity(
    masks: gpd.GeoDataFrame,
    reference: gpd.GeoDataFrame,
    buffer_m: float = 50.0,
    snap_tolerance: float = 10.0,
) -> dict:
    """
    Analyze proximity between masks and reference points.

    Parameters
    ----------
    masks : GeoDataFrame
        Detected PV masks
    reference : GeoDataFrame
        Reference point dataset (prosumers)
    buffer_m : float
        Buffer distance (meters) around masks to search for points
    snap_tolerance : float
        Maximum distance to consider a point "inside" a mask

    Returns
    -------
    dict
        Analysis results with metrics per mask and overall statistics
    """
    if masks.empty or reference.empty:
        return {"error": "Empty masks or reference data"}

    # Ensure same CRS
    if masks.crs != reference.crs:
        reference = reference.to_crs(masks.crs)

    results = {
        "total_masks": len(masks),
        "total_reference_points": len(reference),
        "buffer_m": buffer_m,
        "snap_tolerance": snap_tolerance,
        "masks": [],
        "summary": {},
    }

    matched_points = set()
    true_positives = 0
    false_positives = 0

    for idx, mask_row in masks.iterrows():
        mask_geom = mask_row.geometry

        # Find points inside mask (with snap tolerance)
        buffered = mask_geom.buffer(snap_tolerance)
        points_in_mask = reference[reference.geometry.within(buffered)]

        # Find points within buffer zone
        buffer_zone = mask_geom.buffer(buffer_m)
        points_in_buffer = reference[reference.geometry.within(buffer_zone)]

        n_inside = len(points_in_mask)
        n_in_buffer = len(points_in_buffer)

        # Track matched points
        if n_inside > 0:
            matched_points.update(points_in_mask.index.tolist())
            true_positives += 1
        else:
            false_positives += 1

        mask_result = {
            "mask_id": idx,
            "area_m2": mask_geom.area,
            "points_inside": n_inside,
            "points_in_buffer": n_in_buffer,
            "has_match": n_inside > 0,
            "source_file": mask_row.get("source_file", "unknown"),
        }
        results["masks"].append(mask_result)

    # Find unmatched points (false negatives)
    all_point_indices = set(reference.index.tolist())
    unmatched_points = all_point_indices - matched_points
    false_negatives = len(unmatched_points)

    # Calculate metrics
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    results["summary"] = {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "matched_points": len(matched_points),
        "unmatched_points": len(unmatched_points),
    }

    return results


def generate_report(results: dict, output_path: Path) -> None:
    """Generate analysis report as CSV and JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # CSV per mask
    if results["masks"]:
        df = pd.DataFrame(results["masks"])
        csv_path = output_path / "proximity_analysis.csv"
        df.to_csv(csv_path, index=False)
        click.echo(f"Wrote mask analysis to {csv_path}")

    # JSON summary
    json_path = output_path / "proximity_summary.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    click.echo(f"Wrote summary to {json_path}")

    # Print summary
    summary = results["summary"]
    click.echo("\n=== PROXIMITY ANALYSIS SUMMARY ===")
    click.echo(f"Total masks: {results['total_masks']}")
    click.echo(f"Total reference points: {results['total_reference_points']}")
    click.echo(f"Buffer distance: {results['buffer_m']}m")
    click.echo(f"True positives: {summary['true_positives']}")
    click.echo(f"False positives: {summary['false_positives']}")
    click.echo(f"False negatives: {summary['false_negatives']}")
    click.echo(f"Precision: {summary['precision']}")
    click.echo(f"Recall: {summary['recall']}")
    click.echo(f"F1 Score: {summary['f1']}")


def export_annotated_masks(
    masks: gpd.GeoDataFrame,
    reference: gpd.GeoDataFrame,
    output_path: Path,
    buffer_m: float = 50.0,
) -> None:
    """Export masks with proximity attributes for QGIS visualization."""
    if masks.empty:
        return

    # Add proximity attributes
    annotated = masks.copy()
    annotated["points_inside"] = 0
    annotated["points_in_buffer"] = 0
    annotated["has_match"] = False

    for idx, row in annotated.iterrows():
        geom = row.geometry
        buffered = geom.buffer(10.0)
        points_in = reference[reference.geometry.within(buffered)]
        buffer_zone = geom.buffer(buffer_m)
        points_buf = reference[reference.geometry.within(buffer_zone)]

        annotated.at[idx, "points_inside"] = len(points_in)
        annotated.at[idx, "points_in_buffer"] = len(points_buf)
        annotated.at[idx, "has_match"] = len(points_in) > 0

    # Export
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gpkg_path = output_path / "annotated_masks.gpkg"
    annotated.to_file(gpkg_path, driver="GPKG", layer="masks_with_proximity")
    click.echo(f"Wrote annotated masks to {gpkg_path}")


@click.command()
@click.option(
    "--masks",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Directory containing detected mask GeoJSON files.",
)
@click.option(
    "--reference",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Reference point dataset (GeoPackage/Shapefile).",
)
@click.option(
    "--buffer",
    type=float,
    default=50.0,
    show_default=True,
    help="Buffer distance in meters around masks.",
)
@click.option(
    "--snap-tolerance",
    type=float,
    default=10.0,
    show_default=True,
    help="Snap tolerance for point-in-mask test.",
)
@click.option(
    "--tile-id",
    default=None,
    help="Process only masks for this tile ID.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for analysis results.",
)
def main(masks, reference, buffer, snap_tolerance, tile_id, output):
    """Analyze proximity between detected masks and reference points."""
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


if __name__ == "__main__":
    main()
