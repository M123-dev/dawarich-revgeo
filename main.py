from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Optional
import threading
import math

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
    gpkg_cache_max_tiles: int = 20
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
            gpkg_cache_max_tiles=int(os.getenv("GPKG_CACHE_MAX_TILES", "20")),
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
GPKG_CACHE_MAX_TILES = SETTINGS.gpkg_cache_max_tiles
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
        gpkg_cache_max_tiles=GPKG_CACHE_MAX_TILES,
        gpkg_cache_ttl_seconds=GPKG_CACHE_TTL_SECONDS,
        host=HOST,
        port=PORT,
        api_key=API_KEY,
        api_key_header=API_KEY_HEADER,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_app_state(app)
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
    sindex: object
    last_accessed: float


class TileCache:
    """Thread-safe LRU cache for spatial tiles.

    - Uses an OrderedDict to maintain LRU ordering.
    - Guards structural mutations with a global lock.
    - Uses per-key locks to ensure only one loader builds a tile at a time.
    """

    def __init__(self, max_entries: int = 20, ttl_seconds: int = 900) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired = [
            key
            for key, entry in list(self._entries.items())
            if now - entry.last_accessed > self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)
        if expired:
            _cache_log(f"purged expired cache entries={expired} ttl_seconds={self.ttl_seconds} remaining={self.size()}")

    def get(self, key: str) -> Optional[CacheEntry]:
        with self._lock:
            self._purge_expired_locked()
            entry = self._entries.get(key)
            if entry is None:
                _cache_log(f"cache miss for tile={key} size={self.size()}")
                return None
            entry.last_accessed = time.time()
            self._entries.move_to_end(key)
            _cache_log(f"cache hit for tile={key} size={self.size()}")
            return entry

    def put(self, key: str, gdf: gpd.GeoDataFrame) -> None:
        with self._lock:
            self._purge_expired_locked()
            if key in self._entries:
                self._entries.pop(key)
            # build sindex explicitly
            sidx = getattr(gdf, "sindex", None)
            self._entries[key] = CacheEntry(gdf=gdf, sindex=sidx, last_accessed=time.time())
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                evicted = self._entries.popitem(last=False)
                _cache_log(f"cache evicted tile={evicted[0]} size={self.size()}")
            _cache_log(f"cache stored tile={key} size={self.size()} max_entries={self.max_entries}")

    def acquire_key_lock(self, key: str) -> threading.Lock:
        """Return a per-key lock, creating it if necessary."""
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def remove_key_lock(self, key: str) -> None:
        with self._lock:
            self._key_locks.pop(key, None)


def tile_key_from_coords(lon: float, lat: float, tile_size: float = 1.0) -> str:
    x_idx = math.floor(lon / tile_size)
    y_idx = math.floor(lat / tile_size)
    return f"{y_idx}:{x_idx}:{tile_size}"


def tile_bbox_from_key(key: str, buffer: float = 0.1) -> tuple[float, float, float, float]:
    # key format: "{y_idx}:{x_idx}:{tile_size}"
    parts = key.split(":")
    y_idx = int(parts[0])
    x_idx = int(parts[1])
    tile_size = float(parts[2])
    minx = x_idx * tile_size - buffer
    miny = y_idx * tile_size - buffer
    maxx = (x_idx + 1) * tile_size + buffer
    maxy = (y_idx + 1) * tile_size + buffer
    return (minx, miny, maxx, maxy)


def load_tile_from_gpkg(file_path: str, layer: str, key: str, buffer: float = 0.1) -> Optional[gpd.GeoDataFrame]:
    bbox = tile_bbox_from_key(key, buffer=buffer)
    _cache_log(f"loading tile {key} bbox={bbox}")
    subset = load_gadm(file_path, layer, GPKG_COLUMNS, bbox=bbox)
    if subset.empty:
        _cache_log(f"loaded tile {key} is empty")
        return None
    return subset


def validate_api_key(api_key: str | None, required_key: str | None = None) -> None:
    if not required_key:
        return
    if api_key != required_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def setup_app_state(app: FastAPI) -> None:
    current_settings = get_runtime_settings()
    resolved_path = current_settings.gpkg_path
    print(
        f"Starting reverse geocoder (no global dataset load): dataset={resolved_path} layer={current_settings.gpkg_layer} mode={current_settings.gpkg_cache_mode}",
        flush=True,
    )
    app.state.settings = current_settings
    app.state.cache = TileCache(max_entries=current_settings.gpkg_cache_max_tiles, ttl_seconds=current_settings.gpkg_cache_ttl_seconds)


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

        # Ensure cache exists (should be created during lifespan/setup)
        if not hasattr(app.state, "cache") or app.state.cache is None:
            app.state.cache = TileCache(max_entries=runtime_settings.gpkg_cache_max_tiles, ttl_seconds=runtime_settings.gpkg_cache_ttl_seconds)

        point = Point(lon, lat)
        tile_size = 1.0
        buffer = 0.1
        tile_key = tile_key_from_coords(lon, lat, tile_size=tile_size)
        _cache_log(f"request point=({lon}, {lat}) tile={tile_key} cache_size={app.state.cache.size()}")

        cache: TileCache = app.state.cache

        # Try fast path: cached tile
        entry = cache.get(tile_key)
        if entry is not None:
            sindex = entry.sindex or entry.gdf.sindex
            country, city = find_best_match(entry.gdf, sindex, point)
            if country != "Unknown" or city != "Unknown":
                _cache_log(f"matched against cached tile={tile_key} country={country} city={city}")
                return build_feature(lon, lat, country, city)

        # Acquire per-key loader lock so only one request loads the tile
        key_lock = cache.acquire_key_lock(tile_key)
        try:
            with key_lock:
                # Re-check after acquiring lock
                entry = cache.get(tile_key)
                if entry is not None:
                    sindex = entry.sindex or entry.gdf.sindex
                    country, city = find_best_match(entry.gdf, sindex, point)
                    if country != "Unknown" or city != "Unknown":
                        _cache_log(f"matched against cached tile(after-lock)={tile_key} country={country} city={city}")
                        return build_feature(lon, lat, country, city)

                # Load tile from GeoPackage (small bbox read)
                subset = load_tile_from_gpkg(runtime_settings.gpkg_file, runtime_settings.gpkg_layer, tile_key, buffer=buffer)

                # If the tile is empty (ocean, no polygons), cache an empty GeoDataFrame
                if subset is None or subset.empty:
                    _cache_log(f"loaded tile {tile_key} is empty (caching empty tile)")
                    empty = gpd.GeoDataFrame(columns=GPKG_COLUMNS, geometry="geometry")
                    cache.put(tile_key, empty)
                    return build_feature(lon, lat, "Unknown", "Unknown")

                # Force spatial index creation by accessing .sindex
                _ = subset.sindex
                cache.put(tile_key, subset)

                # Final match against newly cached tile
                new_entry = cache.get(tile_key)
                if new_entry is None:
                    return build_feature(lon, lat, "Unknown", "Unknown")
                sindex = new_entry.sindex or new_entry.gdf.sindex
                country, city = find_best_match(new_entry.gdf, sindex, point)
                return build_feature(lon, lat, country, city)
        finally:
            # Clean up per-key lock to avoid unbounded growth of the key-lock map.
            try:
                cache.remove_key_lock(tile_key)
            except Exception:
                pass

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host=SETTINGS.host, port=SETTINGS.port)
