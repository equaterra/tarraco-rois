FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# Geo system libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev git \
    && rm -rf /var/lib/apt/lists/*

# Python geo + utils stack
COPY requirements.txt .
RUN pip install --no-cache-dir \
    geopandas shapely rasterio fiona pyproj numpy affine \
    click pyyaml pandas Pillow tqdm

# LangSAM (GroundingDINO + SAM2)
RUN pip install --no-cache-dir \
    git+https://github.com/luca-medeiros/lang-segment-anything.git

COPY . /app

ENTRYPOINT ["python"]
CMD ["scripts/run_langsam.py", "--help"]
