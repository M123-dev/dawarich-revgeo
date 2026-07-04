from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Sequence

import geopandas as gpd
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from pandas import Series
from shapely.geometry import Point

GPKG_FILE = os.getenv("GPKG_FILE", "gadm41_DEU.gpkg")
GPKG_LAYER = os.getenv("GPKG_LAYER", "ADM_ADM_4")
GPKG_COLUMNS = ["geometry", "NAME_0", "NAME_1", "NAME_2", "NAME_3", "NAME_4", "COUNTRY"]
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "2322"))
API_KEY = os.getenv("API_KEY") or None
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")

COUNTRY_KEYS = ("COUNTRY", "NAME_0")
STATE_KEYS = ("NAME_4", "NAME_3", "NAME_2", "NAME_1")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_gadm_state()
    yield

app = FastAPI(
    title="Photon-compatible Reverse Geocoder",
    description="Lightweight reverse geocoding service for Dawarich using a local GADM GeoPackage.",
    lifespan=lifespan,
)


def load_gadm(file_path: str, layer: str, columns: list[str]) -> gpd.GeoDataFrame:
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Missing GeoPackage: {file_path}. Please place the file next to this script or set GPKG_FILE."
        )

    return gpd.read_file(file_path, layer=layer, columns=columns)


def get_property(row: Series | gpd.GeoSeries, keys: Sequence[str], fallback: str = "Unknown") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", "nan"):
            return str(value)
    return fallback


def build_feature(lon: float, lat: float, country: str, city: str) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "country": country,
                    "city": city,
                },
            }
        ],
    }


def find_best_match(gdf: gpd.GeoDataFrame, spatial_index, point: Point) -> tuple[str, str]:
    candidate_indexes = list(spatial_index.intersection(point.bounds))
    if not candidate_indexes:
        return "Unknown", "Unknown"

    candidates = gdf.iloc[candidate_indexes]
    exact_matches = candidates[candidates.contains(point)]
    row = exact_matches.iloc[0] if not exact_matches.empty else candidates.iloc[0]

    country = get_property(row, COUNTRY_KEYS, fallback="Unknown")
    city = get_property(row, STATE_KEYS, fallback="Unknown")
    return country, city


def validate_api_key(api_key: str | None) -> None:
    if not API_KEY:
        return
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def init_gadm_state() -> None:
    try:
        gdf = load_gadm(GPKG_FILE, GPKG_LAYER, GPKG_COLUMNS)
    except FileNotFoundError as error:
        raise RuntimeError(str(error)) from error

    app.state.gdf = gdf
    app.state.spatial_index = gdf.sindex


startup_event = init_gadm_state


@app.get("/reverse")
def reverse_geocode(
    lat: float = Query(..., description="Latitude of the query point"),
    lon: float = Query(..., description="Longitude of the query point"),
    radius: float | None = Query(
        None,
        ge=0,
        le=5000,
        description="Optional radius in kilometers. Accepted for compatibility but not used in this implementation.",
    ),
    api_key: str | None = Header(None, alias=API_KEY_HEADER),
) -> dict:
    validate_api_key(api_key)

    if not hasattr(app.state, "gdf") or not hasattr(app.state, "spatial_index"):
        raise HTTPException(status_code=503, detail="Reverse geocoder is not initialized.")

    point = Point(lon, lat)
    country, city = find_best_match(app.state.gdf, app.state.spatial_index, point)
    return build_feature(lon, lat, country, city)


if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT)
