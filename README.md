# Dawarich Reverse Geocoder

A small local reverse geocoding service for Dawarich. It exposes a Photon-compatible `/reverse` endpoint and returns GeoJSON features with only `country` and the most specific admin name (exposed as `city`).

## Design goals

- Keep the app easy to read and maintain
- Move all runtime settings to environment variables
- Avoid hardcoded file paths and local-directory assumptions
- Make the same app runnable locally and in Docker

> [!NOTE]
> You must download the GADM GeoPackage yourself as its redistribution is not allowed.

## Local run

1. Download the GADM GeoPackage for the country/level you need from https://gadm.org/ (follow their license and download terms).
2. Place the `.gpkg` file in a stable location, for example `./datasets/`.
3. Start the service with defaults or with environment variables if you need a different dataset:

```bash
python main.py
```

Or with uv:

```bash
uv run python main.py
```

## Docker run

Edit [docker-compose.yml](docker-compose.yml) and run:

```bash
docker compose up --build
```

The host dataset directory is mounted into the container at `/datasets`, and the app reads the file from `GPKG_FILE=/datasets/gadm_410.gpkg`. 

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

## Test suite

Run the tests with:

```bash
python -m pytest test_main.py
```

## Notes

- The service returns `country` and `city` (most specific available admin name). If a detailed locality is not available, it falls back to the best available admin name.
- The app expects the API key in the header specified by `API_KEY_HEADER`.
- The project is intentionally simple and maintainable, so the geospatial logic remains focused on a single reverse-geocoding endpoint.
