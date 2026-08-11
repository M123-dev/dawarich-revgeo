from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
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
DEFAULT_GPKG_LAYER = "gadm_410"
logger = logging.getLogger(__name__)


def default_gpkg_file() -> str:
    return str((Path(__file__).resolve().parent / "datasets" / "gadm_410.gpkg").resolve())


@dataclass(frozen=True)
class Settings:
    gpkg_file: str = "datasets/gadm_410.gpkg"
    gpkg_layer: str = DEFAULT_GPKG_LAYER
    gpkg_cache_mode: str = "country"
    gpkg_cache_max_countries: int = 3
    gpkg_cache_ttl_seconds: int = 900
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
        raw_layer = (os.getenv("GPKG_LAYER") or DEFAULT_GPKG_LAYER).strip() or DEFAULT_GPKG_LAYER
        raw_cache_mode = (os.getenv("GPKG_CACHE_MODE") or "country").strip().lower()
        raw_cache_mode = raw_cache_mode if raw_cache_mode in {"country", "world"} else "country"
        return cls(
            gpkg_file=os.getenv("GPKG_FILE", default_gpkg_file()),
            gpkg_layer=raw_layer,
            gpkg_cache_mode=raw_cache_mode,
            gpkg_cache_max_countries=int(os.getenv("GPKG_CACHE_MAX_COUNTRIES", "3")),
            gpkg_cache_ttl_seconds=int(os.getenv("GPKG_CACHE_TTL_SECONDS", "900")),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "2322")),
            api_key=os.getenv("API_KEY") or None,
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
        )


SETTINGS = Settings.from_env()
GPKG_FILE = SETTINGS.gpkg_file
GPKG_LAYER = SETTINGS.gpkg_layer
GPKG_CACHE_MODE = SETTINGS.gpkg_cache_mode
GPKG_CACHE_MAX_COUNTRIES = SETTINGS.gpkg_cache_max_countries
GPKG_CACHE_TTL_SECONDS = SETTINGS.gpkg_cache_ttl_seconds
HOST = SETTINGS.host
PORT = SETTINGS.port
API_KEY = SETTINGS.api_key
API_KEY_HEADER = SETTINGS.api_key_header


def get_runtime_settings() -> Settings:
    return Settings(
        gpkg_file=GPKG_FILE,
        gpkg_layer=GPKG_LAYER,
        gpkg_cache_mode=GPKG_CACHE_MODE,
        gpkg_cache_max_countries=GPKG_CACHE_MAX_COUNTRIES,
        gpkg_cache_ttl_seconds=GPKG_CACHE_TTL_SECONDS,
        host=HOST,
        port=PORT,
        api_key=API_KEY,
        api_key_header=API_KEY_HEADER,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_gadm_state()
    yield


def load_gadm(
    file_path: str,
    layer: str,
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

    read_kwargs = {"layer": layer, "columns": columns}
    if bbox is not None:
        read_kwargs["bbox"] = bbox

    try:
        loaded = gpd.read_file(str(resolved_path), **read_kwargs)
        if isinstance(loaded, gpd.GeoDataFrame):
            return loaded
        return gpd.GeoDataFrame(loaded, geometry="geometry")
    except Exception as exc:
        logger.exception(
            "Failed to load GeoPackage layer file=%s layer=%s columns=%s bbox=%s",
            resolved_path,
            layer,
            columns,
            bbox,
        )
        raise RuntimeError(
            f"Configured GPKG_LAYER '{layer}' could not be opened for '{resolved_path}'. Check that the layer exists and matches the dataset schema."
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


def _cache_log(message: str) -> None:
    print(f"[cache] {message}", flush=True)


@dataclass
class CacheEntry:
    gdf: gpd.GeoDataFrame
    last_accessed: float


class CountryCache:
    def __init__(self, max_entries: int = 3, ttl_seconds: int = 900) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    def size(self) -> int:
        return len(self._entries)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            country
            for country, entry in list(self._entries.items())
            if now - entry.last_accessed > self.ttl_seconds
        ]
        for country in expired:
            self._entries.pop(country, None)
        if expired:
            _cache_log(
                f"purged expired cache entries={expired} ttl_seconds={self.ttl_seconds} remaining={self.size()}"
            )

    def get(self, country: str) -> CacheEntry | None:
        self._purge_expired()
        entry = self._entries.get(country)
        if entry is None:
            _cache_log(f"cache miss for country={country} size={self.size()}")
            return None

        entry.last_accessed = time.time()
        self._entries.move_to_end(country)
        _cache_log(f"cache hit for country={country} size={self.size()}")
        return entry

    def put(self, country: str, value: gpd.GeoDataFrame) -> None:
        self._purge_expired()
        if country in self._entries:
            self._entries.pop(country)
        self._entries[country] = CacheEntry(gdf=value, last_accessed=time.time())
        self._entries.move_to_end(country)
        while len(self._entries) > self.max_entries:
            evicted = self._entries.popitem(last=False)
            _cache_log(f"cache evicted country={evicted[0]} size={self.size()}")
        _cache_log(f"cache stored country={country} size={self.size()} max_entries={self.max_entries}")


def _build_bbox(point: Point, size_deg: float = 1.0) -> tuple[float, float, float, float]:
    return (
        point.x - size_deg,
        point.y - size_deg,
        point.x + size_deg,
        point.y + size_deg,
    )


def _load_country_subset(
    file_path: str,
    layer: str,
    point: Point,
) -> tuple[str, gpd.GeoDataFrame] | None:
    bbox = _build_bbox(point, size_deg=1.0)
    _cache_log(f"loading country subset for point=({point.x}, {point.y}) bbox={bbox}")
    subset = load_gadm(file_path, layer, GPKG_COLUMNS, bbox=bbox)
    if subset.empty:
        _cache_log("loaded subset is empty")
        return None

    country, city = find_best_match(subset, subset.sindex, point)
    if country == "Unknown" and city == "Unknown":
        _cache_log("loaded subset did not resolve a country/city")
        return None

    _cache_log(f"resolved country={country} city={city} from subset")
    return country, subset


def validate_api_key(api_key: str | None, required_key: str | None = None) -> None:
    if not required_key:
        return
    if api_key != required_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def init_gadm_state() -> None:
    current_settings = get_runtime_settings()
    try:
        resolved_path = current_settings.gpkg_path
        print(
            f"Starting reverse geocoder: dataset={resolved_path} layer={current_settings.gpkg_layer} mode={current_settings.gpkg_cache_mode}",
            flush=True,
        )
        startup_bbox = (-0.1, -0.1, 0.1, 0.1)
        gdf = load_gadm(current_settings.gpkg_file, current_settings.gpkg_layer, GPKG_COLUMNS, bbox=startup_bbox)
    except FileNotFoundError as error:
        logger.exception("GeoPackage file not found during startup: %s", current_settings.gpkg_path)
        raise RuntimeError(str(error)) from error
    except RuntimeError:
        logger.exception(
            "Reverse geocoder startup failed for dataset=%s layer=%s",
            current_settings.gpkg_path,
            current_settings.gpkg_layer,
        )
        raise

    app.state.settings = current_settings
    app.state.cache = CountryCache(
        max_entries=current_settings.gpkg_cache_max_countries,
        ttl_seconds=current_settings.gpkg_cache_ttl_seconds,
    )
    app.state.gdf = gdf
    app.state.spatial_index = gdf.sindex
    app.state.active_country = None


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

        if not hasattr(app.state, "cache"):
            app.state.cache = CountryCache(
                max_entries=current_settings.gpkg_cache_max_countries,
                ttl_seconds=current_settings.gpkg_cache_ttl_seconds,
            )

        point = Point(lon, lat)
        active_country = getattr(app.state, "active_country", None)
        _cache_log(
            f"request point=({lon}, {lat}) active_country={active_country or '<none>'} cache_size={app.state.cache.size()}"
        )
        cached_entry = app.state.cache.get(active_country) if active_country else None
        if cached_entry is None and hasattr(app.state, "gdf") and hasattr(app.state, "spatial_index") and app.state.gdf is not None:
            country, city = find_best_match(app.state.gdf, app.state.spatial_index, point)
            if country != "Unknown" or city != "Unknown":
                _cache_log(f"matched against startup/global dataset country={country} city={city}")
                return build_feature(lon, lat, country, city)

        if cached_entry is not None:
            cached_gdf = cached_entry.gdf
            country, city = find_best_match(cached_gdf, cached_gdf.sindex, point)
            if country != "Unknown" or city != "Unknown":
                _cache_log(f"matched against cached country={active_country} country={country} city={city}")
                return build_feature(lon, lat, country, city)
            _cache_log(
                f"cached subset for country={active_country} did not match point; loading fresh subset"
            )

        subset_result = _load_country_subset(
            current_settings.gpkg_file,
            current_settings.gpkg_layer,
            point,
        )
        if subset_result is None:
            return build_feature(lon, lat, "Unknown", "Unknown")

        country, gdf = subset_result
        _cache_log(f"caching newly loaded country={country} rows={len(gdf)}")
        app.state.cache.put(country, gdf)
        app.state.gdf = gdf
        app.state.spatial_index = gdf.sindex
        app.state.active_country = country

        country, city = find_best_match(gdf, gdf.sindex, point)
        return build_feature(lon, lat, country, city)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host=SETTINGS.host, port=SETTINGS.port)
