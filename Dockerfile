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
# Forçar versió de transformers compatible amb torch 2.4.1
# (lang-sam s'emporta transformers 5.x per defecte, que trenca amb aquest torch)
RUN pip install --no-cache-dir "transformers==4.46.3"

COPY . /app

CMD ["bash"]
