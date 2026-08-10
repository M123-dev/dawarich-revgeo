from __future__ import annotations

import importlib.util
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from pandas import Series
from shapely.geometry import Point

GPKG_COLUMNS = ["geometry", "NAME_0", "NAME_1", "NAME_2", "NAME_3", "NAME_4", "COUNTRY"]
COUNTRY_KEYS = ("COUNTRY", "NAME_0")
STATE_KEYS = ("NAME_4", "NAME_3", "NAME_2", "NAME_1")


def default_gpkg_file() -> str:
    return str((Path(__file__).resolve().parent / "datasets" / "gadm_410.gpkg").resolve())


@dataclass(frozen=True)
class Settings:
    gpkg_file: str = "datasets/gadm_410.gpkg"
    gpkg_layer: str | None = None
    gpkg_mode: str = "world"
    gpkg_bbox: tuple[float, float, float, float] | None = None
    host: str = "0.0.0.0"
    port: int = 2322
    api_key: str | None = None
    api_key_header: str = "X-API-Key"

    @property
    def gpkg_path(self) -> Path:
        path = Path(self.gpkg_file).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path

    @classmethod
    def from_env(cls) -> "Settings":
        raw_layer = os.getenv("GPKG_LAYER")
        raw_mode = (os.getenv("GPKG_MODE") or "world").strip().lower()
        raw_bbox = os.getenv("GPKG_BBOX")
        parsed_bbox: tuple[float, float, float, float] | None = None

        if raw_bbox:
            parts = [part.strip() for part in raw_bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("GPKG_BBOX must be in the form 'minx,miny,maxx,maxy'.")
            parsed_bbox = tuple(float(value) for value in parts)

        return cls(
            gpkg_file=os.getenv("GPKG_FILE", default_gpkg_file()),
            gpkg_layer=raw_layer if raw_layer else None,
            gpkg_mode=raw_mode if raw_mode in {"world", "region", "global"} else "world",
            gpkg_bbox=parsed_bbox,
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "2322")),
            api_key=os.getenv("API_KEY") or None,
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
        )


SETTINGS = Settings.from_env()
GPKG_FILE = SETTINGS.gpkg_file
GPKG_LAYER = SETTINGS.gpkg_layer
GPKG_MODE = SETTINGS.gpkg_mode
GPKG_BBOX = SETTINGS.gpkg_bbox
HOST = SETTINGS.host
PORT = SETTINGS.port
API_KEY = SETTINGS.api_key
API_KEY_HEADER = SETTINGS.api_key_header


def get_runtime_settings() -> Settings:
    return Settings(
        gpkg_file=GPKG_FILE,
        gpkg_layer=GPKG_LAYER,
        gpkg_mode=GPKG_MODE,
        gpkg_bbox=GPKG_BBOX,
        host=HOST,
        port=PORT,
        api_key=API_KEY,
        api_key_header=API_KEY_HEADER,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_gadm_state()
    yield

# Fallback: When a non standard dataset is used, the layer name may not match the expected GADM layer.
# This function attempts to list available layers and select the most appropriate one.
def _list_gpkg_layers(file_path: str) -> list[str]:
    if importlib.util.find_spec("pyogrio") is None:
        return []

    try:
        pyogrio = importlib.import_module("pyogrio")
        if hasattr(pyogrio, "list_layers"):
            raw = pyogrio.list_layers(file_path)
            if raw is None:
                return []

            raw_list = list(raw)
            if not raw_list:
                return []

            first = raw_list[0]
            if isinstance(first, dict) and "name" in first:
                return [str(item["name"]) for item in raw_list]

            normalized: list[str] = []
            for item in raw_list:
                if isinstance(item, str):
                    normalized.append(item)
                elif isinstance(item, dict):
                    name = item.get("name")
                    if name is not None:
                        normalized.append(str(name))
                elif hasattr(item, "__iter__") and not isinstance(item, (bytes, bytearray)):
                    values = list(item)
                    if values:
                        normalized.append(str(values[0]))
            return normalized
    except Exception:
        pass

    return []

# Select the best matching GADM layer from a list of available layers,
# prioritizing the preferred layer if present.
def _select_gadm_layer(layers: Sequence[str], preferred: str | None = None) -> str:
    if not layers:
        return preferred or ""

    normalized_layers = [layer for layer in layers if isinstance(layer, str)]
    if preferred and preferred in normalized_layers:
        return preferred

    adm_candidates = [
        layer for layer in normalized_layers if "adm" in layer.lower() or "admin" in layer.lower()
    ]
    if adm_candidates:
        for suffix in ("4", "3", "2", "1"):
            for layer in adm_candidates:
                lowered = layer.lower()
                if lowered.endswith(f"_{suffix}") or lowered.endswith(f"adm_{suffix}") or lowered.endswith(suffix):
                    return layer
        return adm_candidates[0]

    if len(normalized_layers) == 1:
        return normalized_layers[0]

    return normalized_layers[0]


def resolve_gadm_layer(file_path: str, preferred: str | None) -> str:
    available_layers = _list_gpkg_layers(file_path)
    if preferred is not None:
        if preferred in available_layers:
            return preferred
        available = ", ".join(available_layers) if available_layers else "(none available)"
        raise RuntimeError(
            f"Configured GPKG_LAYER '{preferred}' is not available in '{file_path}'. Available layers: {available}."
        )

    if not available_layers:
        raise RuntimeError(f"Could not auto-detect a layer in '{file_path}'.")

    return _select_gadm_layer(available_layers)


def load_gadm(
    file_path: str,
    layer: str | None,
    columns: list[str],
    bbox: tuple[float, float, float, float] | None = None,
) -> gpd.GeoDataFrame:
    resolved_path = Path(file_path).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = (Path.cwd() / resolved_path).resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(
            f"Missing GeoPackage: {resolved_path}. Please place the file next to this script or set GPKG_FILE."
        )

    chosen_layer = resolve_gadm_layer(str(resolved_path), layer)
    read_kwargs = {"layer": chosen_layer, "columns": columns}
    if bbox is not None:
        read_kwargs["bbox"] = bbox

    try:
        return gpd.read_file(str(resolved_path), **read_kwargs)
    except Exception as exc:
        if layer is not None:
            raise RuntimeError(
                f"Configured GPKG_LAYER '{layer}' is not available in '{resolved_path}'."
            ) from exc
        raise RuntimeError(
            f"Auto-detected layer '{chosen_layer}' could not be opened for '{resolved_path}': {exc}"
        ) from exc


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
                "properties": {"country": country, "city": city},
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


def validate_api_key(api_key: str | None, required_key: str | None = None) -> None:
    if not required_key:
        return
    if api_key != required_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def init_gadm_state() -> None:
    current_settings = get_runtime_settings()
    try:
        resolved_path = Path(current_settings.gpkg_file).expanduser()
        if not resolved_path.is_absolute():
            resolved_path = (Path.cwd() / resolved_path).resolve()
        effective_layer = resolve_gadm_layer(str(resolved_path), current_settings.gpkg_layer)
        layer_source = "explicit" if current_settings.gpkg_layer is not None else "auto-detected"
        mode = "global" if current_settings.gpkg_mode == "global" else current_settings.gpkg_mode
        print(
            f"Starting reverse geocoder: dataset={resolved_path} layer={effective_layer} mode={mode} source={layer_source} bbox={current_settings.gpkg_bbox}",
            flush=True,
        )
        gdf = load_gadm(
            current_settings.gpkg_file,
            effective_layer,
            GPKG_COLUMNS,
            bbox=current_settings.gpkg_bbox,
        )
    except FileNotFoundError as error:
        raise RuntimeError(str(error)) from error

    app.state.settings = current_settings
    app.state.gdf = gdf
    app.state.spatial_index = gdf.sindex


startup_event = init_gadm_state


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or get_runtime_settings()

    app = FastAPI(
        title="Photon-compatible Reverse Geocoder",
        description="Lightweight reverse geocoding service for Dawarich using a local GADM GeoPackage.",
        lifespan=lifespan,
    )
    app.state.settings = current_settings

    @app.get("/reverse")
    def reverse_geocode(
        lat: float = Query(..., description="Latitude of the query point"),
        lon: float = Query(..., description="Longitude of the query point"),
        api_key: str | None = Header(None, alias=current_settings.api_key_header),
    ) -> dict:
        runtime_settings = getattr(app.state, "settings", current_settings)
        validate_api_key(api_key, runtime_settings.api_key)

        if not hasattr(app.state, "gdf") or not hasattr(app.state, "spatial_index"):
            raise HTTPException(status_code=503, detail="Reverse geocoder is not initialized.")

        point = Point(lon, lat)
        country, city = find_best_match(app.state.gdf, app.state.spatial_index, point)
        return build_feature(lon, lat, country, city)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host=SETTINGS.host, port=SETTINGS.port)
