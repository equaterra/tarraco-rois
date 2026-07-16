# pv-segmentation

Purpose: Prepare 1km UTM tiles, run LangSeg/Segment-Anything pipeline to generate GeoJSON masks of photovoltaic (PV) installations, and provide manual review workflow against the existing prosumers dataset.

Structure:

- `data/tiles/` - place ortho tile files here (GeoPackage or GeoTIFF). Expected on Seagate drive; configurable in `config.yaml`.
- `data/masks/raw/` - raw GeoJSON masks produced by LangSAM.
- `data/masks/validated/` - masks after manual validation.
- `scripts/` - helper scripts: tile extraction, run_langsam.py, evaluate_with_prosumers.py.
- `Dockerfile` - container image for running segmentation tools.
- `requirements.txt` - pip dependencies.

Test tiles: two UTM 1km tiles are used for an initial test: `31TBF6932` (Terra Alta, rural) and `31TCF4158` (Reus, urban).

Usage (local machine):

1. Place ortho GeoPackage tiles on the Seagate disk and set `SEAGATE_PATH` in `config.yaml`.
2. Build Docker image and run `scripts/run_langsam.py` to create raw GeoJSON masks.
3. Run `scripts/evaluate_with_prosumers.py` to compare masks with `gpkg/prosumers-tgn-photointerpretation-2024.gpkg` and produce review CSV.

All files and scripts assume cross-platform Docker usage; see top-level workspace `README.md` for multi-machine instructions.
