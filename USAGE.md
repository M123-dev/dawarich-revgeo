# Dawarich Reverse Geocoder — Usage Reference

This file contains the commands for running the service locally and running tests. Keep it as a lightweight reference.

## Run locally

If you are using `uv`:

```bash
uv run python main.py
```

If you are not using `uv`:

```bash
python main.py
```

## Test suite

Run the tests with:

```bash
uv run pytest test_main.py
```

## Notes

- The app expects the API key in the header specified by `API_KEY_HEADER`.
- The default local API key is `change-me-local-key` unless `API_KEY` is set.
- This file is for reference; regular users generally only need the environment variables listed in `README.md`.

## Docker (build & run)

Build the image (from project root):

```bash
docker build -t daw-revgeo .
```

Run the container (mount your GeoPackage and expose port):

```bash
# Mount local GeoPackage into container and expose port 2322
docker run --rm -p 2322:2322 \
	-v "$(pwd)/gadm41_DEU.gpkg:/app/gadm41_DEU.gpkg" \
	-e API_KEY=change-me-local-key \
	daw-revgeo
```

If you want to run without an API key (open access), omit the `-e API_KEY=...` option.

Example Docker Compose service snippet:

```yaml
services:
	reverse-geocoder:
		image: daw-revgeo
		ports:
			- "2322:2322"
		volumes:
			- ./gadm41_DEU.gpkg:/app/gadm41_DEU.gpkg:ro
		environment:
			- API_KEY=change-me-local-key
			- API_KEY_HEADER=X-API-Key
			- GPKG_LAYER=ADM_ADM_4
```
