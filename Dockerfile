FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev git \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# LangSAM (GroundingDINO + SAM2)
RUN pip install --no-cache-dir \
    git+https://github.com/luca-medeiros/lang-segment-anything.git
RUN pip install --no-cache-dir "transformers==4.46.3"

# Copy project
COPY . /app

# Default command
CMD ["python", "-m", "tarraco_rois", "--help"]
