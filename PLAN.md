# tarraco-rois — Pla del projecte

## Visió general

Sistematitzar la experimentació amb diferents models de segmentació per detectar instal·lacions fotovoltaiques (PV) en ortofotos aèries de Catalunya. L'objectiu és identificar quin model/mòdul ofereix les millors màscares amb el menor esforç manual.

## Principis bàsics

1. **Modularitat**: Cada model/mòdul és independent i intercanviable
2. **Configuració centralitzada**: Config global + config per mòdul
3. **Rutes relatives**: Mai hardcodejar rutes, tot relatiu o via configuració
4. **Docker-first**: Treballar dins Docker sempre que sigui possible
5. **Inputs/Outputs estàndard**: Tots els mòduls accepten GeoPackage/GeoTIFF/COG i tornen GeoJSON
6. **Reproducibilitat**: Metadades completes per a cada execució

## Estructura de carpetes

```
tarraco-rois/
├── README.md                    # Documentació principal
├── PLAN.md                      # Aquest fitxer
├── AGENTS.md                    # Per treballar amb IA
├── config.yaml                  # Configuració global
├── requirements.txt             # Dependencies Python
├── Dockerfile                   # Imatge Docker
├── docker-compose.yml           # Serveis Docker
│
├── modules/                     # Mòdules de processament (versionats)
│   ├── langsam/                 # Mòdul LangSAM
│   │   ├── __init__.py
│   │   ├── config.yaml          # Configuració específica LangSAM
│   │   ├── langsam.py           # Implementació principal
│   │   └── prompts/             # Prompts de text
│   │       └── pv_prompts.txt
│   ├── sam2/                    # Futur: SAM2 pur
│   ├── groundingdino/           # Futur: GroundingDINO pur
│   └── custom/                  # Futur: Model personalitzat
│
├── res/                         # Recursos del projecte (versionats)
│   ├── data/                    # Dades de referència
│   │   ├── gpkg/                # GeoPackages (prosumers, etc.)
│   │   ├── shp/                 # Shapefiles (quadricules, etc.)
│   │   └── tiles/               # Rajoles de test (petites)
│   └── qgis/                    # Projectes QGIS
│       └── test.qgz
│
├── sandbox/                     # Treball temporal (NO versionat)
│   ├── data/                    # Dades pesades (ortofotos)
│   │   └── ortho/               # Ortofotos
│   ├── models/                  # Models descarregats
│   │   └── langsam/             # Pesos LangSAM
│   ├── tiles/                   # Rajoles processades
│   └── temp/                    # Fitxers temporals
│
├── dist/                        # Outputs finals
│   └── masks/                   # Màscares generades
│       ├── {model_name}/        # Per model
│       │   ├── {tile_id}.geojson
│       │   ├── {tile_id}.yaml   # Metadades d'execució
│       │   └── ...
│       └── merged/              # Combinat
│           ├── all_masks.geojson
│           └── summary.csv
│
├── tests/                       # Tests
│   ├── config/                  # Tests de configuració
│   └── langsam/                 # Tests LangSAM
│
└── scripts/                     # Scripts auxiliars
    └── cli.py                   # CLI principal
```

## Mòduls

### LangSAM (actual)
- Combina GroundingDINO + SAM2
- Accepta prompts de text en anglès
- Configuració: box_threshold, text_threshold, prompts

### Futurs mòduls
- **SAM2**: Segment Anything Model 2 sense grounding
- **GroundingDINO**: Object detection per prompts de text
- **Custom**: Model entrenat o fine-tuned

## CLI

```bash
# Executar un mòdul
python -m tarraco_rois run --module langsam --ortho sandbox/data/ortho/ortofoto.tif

# Executar múltiples mòduls
python -m tarraco_rois run --module langsam,sam2 --ortho sandbox/data/ortho/ortofoto.tif

# Amb paràmetres específics del mòdul
python -m tarraco_rois run --module langsam --ortho ... --params box_threshold=0.4

# Llistar mòduls disponibles
python -m tarraco_rois modules

# Validar configuració
python -m tarraco_rois config --check
```

## Configuració

### Global (config.yaml)
```yaml
project_name: tarraco-rois
ortho_path: sandbox/data/ortho/  # Ruta relativa al projecte
output_path: dist/masks/
```

### Per mòdul (modules/{name}/config.yaml)
```yaml
module: langsam
box_threshold: 0.35
text_threshold: 0.3
prompts:
  - "solar panel."
  - "photovoltaic panel."
```

## Fases

### Fase 1: Reestructuració (actual)
- [x] Dissenyar estructura de carpetes
- [ ] Crear carpetes i moure fitxers
- [ ] Crear CLI base
- [ ] Crear sistema de configuració
- [ ] Actualitzar Docker

### Fase 2: Validació LangSAM
- [ ] Tests de configuració
- [ ] Test amb 2 rajoles de test
- [ ] Validar outputs (GeoJSON + metadades)

### Fase 3: Experimentació
- [ ] Provar altres models
- [ ] Comparar resultats
- [ ] Optimitzar paràmetres

### Fase 4: Producció
- [ ] Escalar a tot Catalunya
- [ ] Distribució via Zenodo/altres
- [ ] Documentació final
