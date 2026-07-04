FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# System dependencies for geopandas / GDAL
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       gdal-bin \
       libgdal-dev \
       proj-bin \
       libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# Ensure pip, wheel
RUN python -m pip install --upgrade pip wheel

WORKDIR /app

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY . /app

EXPOSE 2322

# Default envs (override at runtime)
ENV GPKG_FILE=gadm41_DEU.gpkg
ENV GPKG_LAYER=ADM_ADM_4
ENV HOST=0.0.0.0
ENV PORT=2322
ENV API_KEY=
ENV API_KEY_HEADER=X-API-Key

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2322"]
