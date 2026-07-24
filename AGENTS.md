# AGENTS.md — Guia per treballar amb IA en aquest projecte

## Visió del projecte

tarraco-rois és un sistema modular per detectar instal·lacions fotovoltaiques (PV) en ortofotos aèries de Catalunya utilitzant models de segmentació amb prompts de text.

## Estructura clau

- `modules/` — Mòdules de processament independents (langsam, sam2, etc.)
- `res/` — Recursos del projecte (dades de referència, shapefiles)
- `sandbox/` — Treball temporal (ortofotos, models descarregats)
- `dist/` — Outputs finals (màscares GeoJSON)
- `config.yaml` — Configuració global

## Convencions de codi

### Python
- Estil: PEP 8
- Type hints obligatoris
- Docstrings per a totes les funcions públiques
- Utilitzar `pathlib.Path` per a rutes
- CLI amb `click`

### Configuració
- Tot ha de ser configurable via `config.yaml` o arguments CLI
- Mai hardcodejar rutes
- Rutes relatives al directori del projecte

### Mòduls
- Cada mòdul és independent
- Tots accepten: GeoPackage, GeoTIFF, COG
- Tots tornen: GeoJSON + YAML de metadades
- Configuració: global + específica del mòdul

### Outputs
- GeoJSON: geometries + propietats bàsiques
- YAML: metadades d'execució (model, paràmetres, temps, etc.)
- Estructura: `dist/masks/{model_name}/{tile_id}.geojson`

## Comandes útils

```bash
# Executar LangSAM en 2 rajoles de test
python -m tarraco_rois run --module langsam --ortho sandbox/data/ortho/ortofoto.tif --tiles res/data/tiles/test/

# Validar configuració
python -m tarraco_rois config --check

# Llistar mòduls
python -m tarraco_rois modules

# Executar dins Docker
docker compose run segmentation python -m tarraco_rois run --module langsam
```

## Errors comuns

1. **Ruta no trobada**: Verificar que `ortho_path` al config.yaml apunta a una ruta vàlida
2. **Mòdul no trobat**: Assegurar-se que el mòdul existeix a `modules/`
3. **Memòria insuficient**: Reduir `box_threshold` o processar menys rajoles

## Quan modifies codi

1. Mantenir la compatibilitat amb inputs/outputs existents
2. Afegir tests per a funcions noves
3. Actualitzar docstrings
4. Verificar que el CLI funciona

## Arxius importants

- `config.yaml` — Configuració central
- `modules/langsam/config.yaml` — Config LangSAM
- `modules/langsam/langsam.py` — Implementació principal
- `scripts/cli.py` — CLI principal
