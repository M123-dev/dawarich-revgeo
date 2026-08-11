# Dawarich Reverse Geocoder

A small local reverse geocoding service for Dawarich. It exposes a Photon-compatible `/reverse` endpoint and returns GeoJSON features with only `country` and the most specific admin name (exposed as `city`).


# What and why

[Dawarich](https://dawarich.app/) can be configured to use a local reverse‑geocoding service to enrich visited places. You can self‑host a full service like [Photon by Komoot](https://photon.komoot.io/), which provides rich OpenStreetMap data but requires substantial storage (roughly 10 GB for Germany and ~60 GB for a full global dump). Using a hosted service sends your location history to a third party and can raise privacy and usage concerns.

This project provides the same API while using a much smaller dataset from [GADM](https://gadm.org/index.html) (administrative boundaries and subdivisions). That typically results in a ~3 GB GeoPackage and — thanks to lazy loading of subsets — a much smaller runtime memory footprint.

> [!NOTE]
> You must manually download the GADM GeoPackage yourself because redistribution is not permitted by its license. ([Read](https://gadm.org/license.html)).

## Setup

1. Download the GADM **GeoPackage** for the country/level you need from gadm.org (follow their license and download terms). If you plan to travel opt for the [whole world dataset](https://gadm.org/download_world.html)

2. Place the `.gpkg` file in a stable location, for example `./datasets/`.

3. Start the service with defaults or with environment variables set. Edit [docker-compose.yml](docker-compose.yml) and run:

```bash
docker compose up --build
```

The host dataset directory is mounted into the container at `/datasets`, and the app reads the file from `GPKG_FILE=/datasets/gadm_410.gpkg`. 

## Using this service with Dawarich

If you run the Dawarich web app and want it to use this local reverse-geocoder instead of an external provider, set the same environment variables Dawarich expects for a Photon-like service. In your Dawarich `docker-compose.yml` (or other deployment manifest) set the photon-related variables to point at this service and provide the API key you configured for the reverse-geocoder.

Example snippet for `docker-compose.yml` (merge into the `services` entries for `dawarich_app` and `dawarich_sidekiq`):

```yaml
services:
  dawarich_app:
    image: freikin/dawarich:latest
    environment:
      RAILS_ENV: production
      APPLICATION_PROTOCOL: http
      # CHANGE THE FOLLOWING
      PHOTON_API_HOST: dawarich-reverse-geocoder:2322
      PHOTON_API_KEY: "change-me-local-key"  # must match `API_KEY` of the reverse-geocoder service
      PHOTON_API_USE_HTTPS: false

  dawarich_sidekiq:
    image: freikin/dawarich:latest
    environment:
      RAILS_ENV: production
      APPLICATION_PROTOCOL: http
      # HERE AS WELL
      PHOTON_API_HOST: dawarich-reverse-geocoder:2322
      PHOTON_API_KEY: "change-me-local-key"
      PHOTON_API_USE_HTTPS: false
```

Notes:
- Use the service name and port that you expose in your runtime. If you run the reverse-geocoder on the same Compose network as Dawarich, the Compose service name `dawarich-reverse-geocoder` (as used in this repo) works.
- Ensure `PHOTON_API_KEY` matches the `API_KEY` set in this project's `docker-compose.yml` so the Dawarich app can authenticate with the local reverse-geocoder.

Also have a look at the [official Dawarich docs](https://dawarich.app/docs/self-hosting/configuration/reverse-geocoding/#how-to-enable-reverse-geocoding)

## Required environment variables

| Variable | Required? | Default | Allowed values | Description |
|---|---|---|---|---|
| `GPKG_FILE` | Yes | `datasets/gadm_410.gpkg` | Any valid path | Path to the GADM GeoPackage file. In Docker this should be set explicitly in Compose and is the source of truth. |
| `GPKG_LAYER` | No | unset | Any layer name present in the GeoPackage | Explicit layer override. If unset, the app inspects the file and picks the best matching layer automatically. |
| `GPKG_CACHE_MODE` | No | `country` | `country`, `world` | Cache strategy for loaded geometry subsets. `country` keeps the working set small; `world` uses the full dataset. |
| `GPKG_CACHE_MAX_COUNTRIES` | No | `3` | Any positive integer | Maximum number of cached country subsets to retain in memory. |
| `GPKG_CACHE_TTL_SECONDS` | No | `86400` | Any positive integer | Time-to-live for cached country subsets in seconds before they expire. |
| `HOST` | No | `0.0.0.0` | Any bind address | Bind address for the HTTP server. |
| `PORT` | No | `2322` | Any valid TCP port | HTTP port for the service. |
| `API_KEY` | No | unset | Any string | Optional API key. If unset, the API is open. |
| `API_KEY_HEADER` | No | `X-API-Key` | Any header name | Header name carrying the API key. |

## API usage

Example request:

```bash
curl "http://localhost:2322/reverse?lat=52.5170365&lon=13.3888599" \
  -H "X-API-Key: change-me-local-key"
```


## Run locally

```bash
python main.py
```

Or with uv:

```bash
uv run python main.py
```

## Test suite

Run the tests with:

```bash
python -m pytest test_main.py
```