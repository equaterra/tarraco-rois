# georefocus — Pla del projecte

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
georefocus/
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
python -m georefocus run --module langsam --ortho sandbox/data/ortho/ortofoto.tif

# Executar múltiples mòduls
python -m georefocus run --module langsam,sam2 --ortho sandbox/data/ortho/ortofoto.tif

# Amb paràmetres específics del mòdul
python -m georefocus run --module langsam --ortho ... --params box_threshold=0.4

# Llistar mòduls disponibles
python -m georefocus modules

# Validar configuració
python -m georefocus config --check
```

## Configuració

### Global (config.yaml)
```yaml
project_name: georefocus
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

### Fase 1: Reestructuració ✅ COMPLETADA
- [x] Dissenyar estructura de carpetes
- [x] Crear carpetes i moure fitxers
- [x] Crear CLI base
- [x] Crear sistema de configuració
- [x] Actualitzar Docker
- [x] Smoketest de validació

### Fase 1.5: Descàrrega d'ortofotos ✅ COMPLETADA
- [x] Crear scripts/fetch_ortho.py
- [x] Suport WMS ICGC (Catalunya, 25cm) i IGN PNOA (Espanya)
- [x] Detecció automàtica de servei segons ubicació
- [x] Conversió CRS automàtica (EPSG:4326 → UTM)
- [x] Validació de descàrregues
- [x] Integració al CLI principal
- [x] Configuració WMS a config.yaml
- [x] Prova real: 1km² Reus → 45.8 MB GeoTIFF (4000x4000 px)

### Fase 2: Validació LangSAM (en curs)
- [x] Crear test d'integració LangSAM
- [x] Verificar estructures de sortida (GeoJSON + metadades)
- [x] Descarregar ortofoto de test (fetch_ortho)
- [ ] Verificar que Docker funciona: `docker compose build`
- [ ] Executar LangSAM en 2 rajoles de test
- [ ] Verificar CLI: `python -m georefocus modules`
- [ ] Verificar CLI: `python -m georefocus config --check`

### Fase 3: Experimentació
- [ ] Provar altres models (SAM2, GroundingDINO)
- [ ] Comparar resultats entre models
- [ ] Optimitzar paràmetres (box_threshold, text_threshold)
- [ ] Afegir nous prompts de detecció
- [ ] Testar filtres d'àrea i merge de polígons

### Fase 4: Producció
- [ ] Escalar a tot Catalunya (quadrícula completa)
- [ ] Optimitzar rendiment (memòria, paral·lelització)
- [ ] Distribució via Zenodo/altres
- [ ] Documentació final

### Fase 5: Anàlisi i Report (condicional)
> Aquesta fase depèn de tenir una capa de geometries de referència
> (punts de prosumers o màscares manuals) per comparar amb les deteccions.

- [x] Crear mòdul proximity.py
- [x] Implementar anàlisi de proximitat punts vs màscares
- [x] Métriques: precision, recall, F1, TP/FP/FN
- [x] Export: CSV + JSON + GeoPackage anotat
- [ ] Importar capa de geometries de referència (prosumers)
- [ ] Executar anàlisi amb dades reals
- [ ] Generar report de qualitat per rajola
- [ ] Visualització QGIS amb resultats d'anàlisi
- [ ] Decidir si l'anàlisi de proximitat és suficient o cal
      entrenar un model propi amb les dades de validació
