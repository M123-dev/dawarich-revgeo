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

| Variable | Default | Description |
|---|---|---|
| `GPKG_FILE` | `datasets/gadm_410.gpkg` | Path to the GADM GeoPackage file. In Docker this should be set explicitly in Compose and is the source of truth. |
| `GPKG_LAYER` | unset / optional | Explicit layer override. If unset, the app inspects the file and picks the best matching layer automatically. |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `2322` | HTTP port |
| `API_KEY` | unset | Optional API key; if unset, the API is open |
| `API_KEY_HEADER` | `X-API-Key` | Header name carrying the API key |

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
