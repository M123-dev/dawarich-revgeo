# Dawarich Reverse Geocoder


A small local reverse geocoding service for Dawarich. It exposes a Photon-compatible `/reverse` endpoint and returns GeoJSON features with only `country` and the most specific admin name (exposed as `city`).


## How to run

> [!NOTE]  
> You must download the GADM GeoPackage yourself as its redistribution is not allowed.

1. Download the GADM GeoPackage for the country/level you need from https://gadm.org/ (follow their license and download terms).
2. Place the `.gpkg` file next to the project (or somewhere on your host).
3. Run with Docker mounting the file read-only:

```bash
docker run --rm -p 2322:2322 \
	-v "$(pwd)/gadm41_DEU.gpkg:/app/gadm41_DEU.gpkg:ro" \
	-e API_KEY=your_key_here \
	daw-revgeo
```

Or use the provided `docker-compose.yml` which mounts `./gadm41_DEU.gpkg` into the container.


## Required environment variables

| Variable | Default | Description |
|---|---|---|
| `GPKG_FILE` | `gadm41_DEU.gpkg` | Path to the GADM GeoPackage file |
| `GPKG_LAYER` | `ADM_ADM_4` | GeoPackage layer name |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `2322` | HTTP port |
| `API_KEY` | - | API key for request authentication; optional (if unset, API is open locally) |
| `API_KEY_HEADER` | `X-API-Key` | Header name carrying the API key |


## Notes

- The service returns `country` and `city` (most specific available admin name). If a detailed locality is not available, the service will return the best available admin name.
- The `radius` query parameter is accepted for compatibility with Photon but is not used by this simple implementation.
- See `USAGE.md` for run and test commands and the Docker usage examples.
