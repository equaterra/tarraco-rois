#!/usr/bin/env python3
"""
run_all_models.py — Run all PV detection models on a building masked GeoTIFF.

Usage:
    python scripts/run_all_models.py sandbox/data/building_pv/building_125534_masked.tif --building-id 125534
"""
import json
import logging
import sys
import time
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / "modules").is_dir():
    PROJECT_ROOT = Path("/app")
sys.path.insert(0, str(PROJECT_ROOT))

# models: (name, module_import_path, run_function_name, config)
MODELS = [
    {
        "name": "langsam",
        "import": "modules.langsam.langsam",
        "run_fn": "run_langsam_file",
    },
    {
        "name": "owlv2",
        "import": "modules.owlv2.owlv2",
        "run_fn": "run_owlv2",
    },
    {
        "name": "clipseg",
        "import": "modules.clipseg.clipseg",
        "run_fn": "run_clipseg",
    },
]


def run_all_models(tif_path: Path, building_id: str = "building", skip: list | None = None):
    skip = skip or []
    t0 = time.time()
    all_results = {}

    output_root = PROJECT_ROOT / "dist" / "masks"
    output_root.mkdir(parents=True, exist_ok=True)

    for model_cfg in MODELS:
        name = model_cfg["name"]
        if name in skip:
            log.info("Skipping %s", name)
            continue

        log.info("=" * 60)
        log.info("Running model: %s", name)
        log.info("=" * 60)

        try:
            mod = __import__(model_cfg["import"], fromlist=[model_cfg["run_fn"]])
            run_fn = getattr(mod, model_cfg["run_fn"])
            model_output = output_root / name
            model_output.mkdir(parents=True, exist_ok=True)

            meta = run_fn(
                tif_path=tif_path,
                output_dir=model_output,
                building_id=building_id,
            )
            all_results[name] = meta
            log.info("[%s] done: %d detections in %.1fs",
                     name, meta.get("detections", 0), meta.get("time_s", 0))
        except Exception as e:
            log.error("[%s] FAILED: %s", name, e, exc_info=True)
            all_results[name] = {"error": str(e)}

    total_time = time.time() - t0

    # Write summary log
    summary = {
        "tif_path": str(tif_path),
        "building_id": building_id,
        "total_time_s": round(total_time, 2),
        "models": all_results,
    }
    log_path = output_root / "test_summary.yaml"
    with open(log_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)
    log.info("Summary written to %s", log_path)

    # Print table
    print()
    print(f"{'Model':<12} {'Detections':<12} {'Time (s)':<10} {'Raw':<8} {'Status'}")
    print("-" * 55)
    for name, result in all_results.items():
        dets = result.get("detections", "N/A")
        ts = result.get("time_s", "N/A")
        raw = result.get("raw_detections", "N/A")
        err = result.get("error")
        status = "OK" if not err else f"ERROR"
        print(f"{name:<12} {str(dets):<12} {str(ts):<10} {str(raw):<8} {status}")
        if err:
            print(f"  └─ {err}")

    return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run all PV detection models")
    parser.add_argument("tif_path", type=str, help="Path to masked building GeoTIFF")
    parser.add_argument("--building-id", type=str, default="building", help="Building ID")
    parser.add_argument("--skip", type=str, nargs="*", default=[], help="Models to skip")
    args = parser.parse_args()
    run_all_models(Path(args.tif_path), args.building_id, args.skip)
