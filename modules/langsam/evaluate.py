"""Compare raw GeoJSON masks with the prosumers point dataset to help manual review.

Outputs a CSV with counts of prosumers within each mask, precision/recall metrics,
and a GeoPackage with annotated masks for QGIS review.
"""
import geopandas as gpd
import pandas as pd
from pathlib import Path
import click

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
import yaml

config = yaml.safe_load(open(CONFIG_PATH))

RAW_DIR = PROJECT_ROOT / config.get("output_path", "dist/masks") / "langsam"
PROSUMERS = PROJECT_ROOT / "res" / "data" / "gpkg" / "prosumers-tgn-photointerpretation-2024.gpkg"


def evaluate_tile(tile_id: str) -> tuple[pd.DataFrame | None, int]:
    """Evaluate a single tile. Returns (DataFrame, total_prosumers_in_tile)."""
    core_file = RAW_DIR / f"{tile_id}_core.geojson"
    donut_file = RAW_DIR / f"{tile_id}_donut.geojson"

    mask_files = []
    if core_file.exists():
        mask_files.append(("core", core_file))
    if donut_file.exists():
        mask_files.append(("donut", donut_file))

    if not mask_files:
        click.echo(f"No mask files found for tile {tile_id}")
        return None, 0

    pros = gpd.read_file(PROSUMERS)

    rows = []
    matched_prosumer_ids = set()
    total_prosumers_in_tile = 0

    for region, mask_file in mask_files:
        masks = gpd.read_file(mask_file)
        if masks.empty:
            continue

        pros_proj = pros.to_crs(masks.crs)

        # Find prosumers within this tile's bounding box
        tile_bbox = masks.total_bounds
        from shapely.geometry import box
        tile_poly = box(*tile_bbox)
        pros_in_tile = pros_proj[pros_proj.geometry.within(tile_poly)]
        total_prosumers_in_tile = max(total_prosumers_in_tile, len(pros_in_tile))

        for idx, row in masks.iterrows():
            geom = row.geometry
            mask_poly = pros_proj[pros_proj.geometry.within(geom)]
            count = len(mask_poly)

            # Track unique prosumer IDs matched
            if "ID" in pros_proj.columns:
                matched_prosumer_ids.update(mask_poly["ID"].tolist())

            area = geom.area
            avg_score = row.get("avg_score", 0) if hasattr(row, "get") else 0

            rows.append(
                {
                    "tile_id": tile_id,
                    "region": region,
                    "mask_id": idx,
                    "area_m2": round(area, 2),
                    "prosumers_inside": int(count),
                    "avg_score": avg_score,
                }
            )

    if not rows:
        click.echo(f"No masks found for tile {tile_id}")
        return None, 0

    df = pd.DataFrame(rows)
    df["matched_prosumers"] = df["prosumers_inside"].cumsum()
    return df, total_prosumers_in_tile


def compute_metrics(matched: int, total: int, total_masks: int) -> dict:
    """Compute precision, recall, F1."""
    if total_masks == 0:
        return {"precision": 0, "recall": 0, "f1": 0, "false_positives": 0}

    # Approximate: matched = true positives, total_masks - matched = false positives
    true_positives = matched
    false_positives = max(0, total_masks - matched)
    false_negatives = max(0, total - matched)

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "true_positives": true_positives,
        "false_positives": false_positives,
    }


@click.command()
@click.option("--tile-id", default=None, help="Process only this tile ID. If omitted, process all tiles.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None,
              help="Output directory for review files (default: dist/masks/langsam/).")
def main(tile_id, output_dir):
    """Compare detected PV masks against prosumer ground-truth dataset."""
    out_dir = output_dir or PROJECT_ROOT / "data" / "masks" / "validated"
    out_dir.mkdir(parents=True, exist_ok=True)

    if tile_id:
        tile_ids = [tile_id]
    else:
        core_files = sorted(RAW_DIR.glob("*_core.geojson"))
        tile_ids = [f.stem.replace("_core", "") for f in core_files]
        if not tile_ids:
            click.echo("No tile masks found in raw directory.")
            return
        click.echo(f"Found {len(tile_ids)} tiles to evaluate.\n")

    all_dfs = []
    for tid in tile_ids:
        click.echo(f"Evaluating {tid}...")
        df, total_pros = evaluate_tile(tid)
        if df is not None:
            click.echo(f"  Masks: {len(df)}, Prosumers in tile: {total_pros}")
            all_dfs.append(df)

    if not all_dfs:
        click.echo("No results to output.")
        return

    combined = pd.concat(all_dfs, ignore_index=True)
    out_csv = out_dir / "evaluation_review.csv"
    combined.to_csv(out_csv, index=False)
    click.echo(f"\nWrote review CSV to {out_csv}")

    # Metrics per tile
    metrics_rows = []
    for tid in tile_ids:
        tid_df = combined[combined["tile_id"] == tid]
        if tid_df.empty:
            continue
        total_masks = len(tid_df)
        matched = tid_df["prosumers_inside"].sum()
        metrics = compute_metrics(int(matched), 0, total_masks)
        metrics["tile_id"] = tid
        metrics["total_masks"] = total_masks
        metrics["total_prosumers_matched"] = int(matched)
        metrics_rows.append(metrics)

    if metrics_rows:
        metrics_df = pd.DataFrame(metrics_rows)
        out_metrics = out_dir / "metrics.csv"
        metrics_df.to_csv(out_metrics, index=False)
        click.echo(f"Wrote metrics to {out_metrics}")

        click.echo("\n=== EVALUATION METRICS ===")
        click.echo(metrics_df.to_string(index=False))

    # Export annotated GeoPackage for QGIS
    gdfs = []
    for tid in tile_ids:
        core_file = RAW_DIR / f"{tid}_core.geojson"
        if core_file.exists():
            gdf = gpd.read_file(core_file)
            if not gdf.empty:
                gdfs.append(gdf)

    if gdfs:
        merged_gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), geometry="geometry")
        out_gpkg = out_dir / "annotated_masks.gpkg"
        merged_gdf.to_file(out_gpkg, driver="GPKG", layer="masks")
        click.echo(f"Wrote annotated GeoPackage to {out_gpkg}")


if __name__ == "__main__":
    main()
