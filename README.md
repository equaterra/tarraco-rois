# tarraco-rois

Sistema modular per detectar instal·lacions fotovoltaiques (PV) en ortofotos aèries de Catalunya.

## Què fa

Utilitza models de segmentació amb prompts de text (LangSAM, SAM2, etc.) per detectar automàticament panells solars en imatges aèries de 25cm de resolució.

## Estructura

```
tarraco-rois/
├── modules/          # Mòdules de processament (versionats)
├── res/              # Recursos del projecte (versionats)
├── sandbox/          # Treball temporal (NO versionat)
├── dist/             # Outputs finals
├── tests/            # Tests
├── config.yaml       # Configuració global
├── PLAN.md           # Pla del projecte
└── AGENTS.md         # Guia per IA
```

## Inici ràpid

### 1. Configurar

Edita `config.yaml` amb les rutes del teu sistema:

```yaml
project_name: tarraco-rois
ortho_path: sandbox/data/ortho/  # Ruta a les ortofotos
output_path: dist/masks/         # Ruta de sortida
```

### 2. Instal·lar dependencies

```bash
pip install -r requirements.txt
```

### 3. Executar

```bash
# Executar LangSAM en 2 rajoles de test
python -m tarraco_rois run --module langsam

# Executar amb paràmetres específics
python -m tarraco_rois run --module langsam --ortho path/to/ortho.tif --tiles path/to/tiles/
```

### 4. Docker (recomanat)

```bash
docker compose build
docker compose run segmentation python -m tarraco_rois run --module langsam
```

## CLI

```bash
# Executar mòdul(s)
python -m tarraco_rois run --module langsam
python -m tarraco_rois run --module langsam,sam2

# Llistar mòduls disponibles
python -m tarraco_rois modules

# Validar configuració
python -m tarraco_rois config --check

# Ajuda
python -m tarraco_rois --help
```

## Mòduls

### LangSAM (actual)
- Combina GroundingDINO + SAM2
- Accepta prompts de text en anglès
- Configuració: `modules/langsam/config.yaml`

### Futurs mòduls
- SAM2 pur
- GroundingDINO pur
- Model personalitzat

## Configuració

### Global (`config.yaml`)
```yaml
project_name: tarraco-rois
ortho_path: sandbox/data/ortho/
output_path: dist/masks/
```

### Per mòdul (`modules/{name}/config.yaml`)
```yaml
module: langsam
box_threshold: 0.35
text_threshold: 0.3
prompts:
  - "solar panel."
  - "photovoltaic panel."
```

## Outputs

Cada execució genera:
- `dist/masks/{model_name}/{tile_id}.geojson` — Màscares
- `dist/masks/{model_name}/{tile_id}.yaml` — Metadades
- `dist/masks/merged/all_masks.geojson` — Combinat
- `dist/masks/merged/summary.csv` — Resum

## Tests

```bash
# Executar tots els tests
pytest tests/

# Tests específics
pytest tests/config/
pytest tests/langsam/
```

## Desenvolupament

```bash
# Instal·lar en mode desenvolupament
pip install -e .

# Linting
ruff check .

# Format
ruff format .
```

## Llicència

Privada — ús intern.
