from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon

import main


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    polygon = Polygon([(13.3, 52.4), (13.5, 52.4), (13.5, 52.6), (13.3, 52.6)])
    return gpd.GeoDataFrame(
        [
            {
                "NAME_0": "Germany",
                "NAME_1": "Berlin",
                "NAME_2": "Mitte",
                "NAME_3": "District",
                "NAME_4": "Berlin",
                "COUNTRY": "Germany",
                "geometry": polygon,
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, sample_gdf: gpd.GeoDataFrame) -> TestClient:
    main.API_KEY = "test-key"
    monkeypatch.setattr(main, "load_gadm", lambda *_args, **_kwargs: sample_gdf)
    app = main.create_app(settings=main.Settings(api_key="test-key"))
    return TestClient(app)


def test_build_feature_shape() -> None:
    feature = main.build_feature(13.3888599, 52.5170365, "Germany", "Berlin")

    assert feature["type"] == "FeatureCollection"
    assert len(feature["features"]) == 1
    assert feature["features"][0]["properties"]["country"] == "Germany"
    assert feature["features"][0]["properties"]["city"] == "Berlin"
    assert feature["features"][0]["geometry"]["coordinates"] == [13.3888599, 52.5170365]


def test_get_property_prefers_first_key(sample_gdf: gpd.GeoDataFrame) -> None:
    row = sample_gdf.iloc[0]
    value = main.get_property(row, ("NAME_0", "COUNTRY"), fallback="Unknown")
    assert value == "Germany"


def test_get_property_returns_fallback(sample_gdf: gpd.GeoDataFrame) -> None:
    blank_row = sample_gdf.iloc[0].copy()
    blank_row["NAME_0"] = ""
    blank_row["COUNTRY"] = None

    value = main.get_property(blank_row, ("NAME_0", "COUNTRY"), fallback="Unknown")
    assert value == "Unknown"


def test_find_best_match_returns_exact(sample_gdf: gpd.GeoDataFrame) -> None:
    point = Point(13.4, 52.5)
    country, state = main.find_best_match(sample_gdf, sample_gdf.sindex, point)

    assert country == "Germany"
    assert state == "Berlin"


def test_find_best_match_uses_most_specific_admin_name(sample_gdf: gpd.GeoDataFrame) -> None:
    point = Point(13.4, 52.5)
    country, state = main.find_best_match(sample_gdf, sample_gdf.sindex, point)

    assert state == "Berlin"


def test_find_best_match_no_candidates_returns_unknown(sample_gdf: gpd.GeoDataFrame) -> None:
    point = Point(0.0, 0.0)
    country, state = main.find_best_match(sample_gdf, sample_gdf.sindex, point)

    assert country == "Unknown"
    assert state == "Unknown"


def test_reverse_geocode_endpoint_returns_geojson(client: TestClient) -> None:
    response = client.get(
        "/reverse",
        params={"lat": 52.5, "lon": 13.4},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["properties"]["country"] == "Germany"
    assert payload["features"][0]["properties"]["city"] == "Berlin"


def test_reverse_geocode_falls_back_to_fresh_subset_when_cached_subset_does_not_cover_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_polygon = Polygon([(6.9, 51.1), (7.1, 51.1), (7.1, 51.3), (6.9, 51.3)])
    cached_gdf = gpd.GeoDataFrame(
        [{"NAME_0": "Germany", "NAME_1": "Haan", "NAME_2": "", "NAME_3": "", "NAME_4": "", "COUNTRY": "Germany", "geometry": cached_polygon}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    fresh_polygon = Polygon([(13.3, 52.4), (13.5, 52.4), (13.5, 52.6), (13.3, 52.6)])
    fresh_gdf = gpd.GeoDataFrame(
        [{"NAME_0": "Germany", "NAME_1": "Berlin", "NAME_2": "Mitte", "NAME_3": "District", "NAME_4": "Berlin", "COUNTRY": "Germany", "geometry": fresh_polygon}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    monkeypatch.setattr(main, "load_gadm", lambda *_args, **_kwargs: fresh_gdf)
    app = main.create_app(settings=main.Settings(api_key="test-key"))
    # compute the tile key for the query point and pre-populate the tile cache
    tile_key = main.tile_key_from_coords(13.4, 52.5, tile_size=1.0)
    app.state.cache = main.TileCache(max_entries=3, ttl_seconds=900)
    app.state.cache.put(tile_key, cached_gdf)

    with TestClient(app) as client:
        response = client.get(
            "/reverse",
            params={"lat": 52.5, "lon": 13.4},
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["features"][0]["properties"]["country"] == "Germany"
    assert payload["features"][0]["properties"]["city"] == "Berlin"


def test_reverse_geocode_endpoint_requires_api_key(client: TestClient) -> None:
    response = client.get("/reverse", params={"lat": 52.5, "lon": 13.4})
    assert response.status_code == 401


def test_reverse_geocode_endpoint_rejects_invalid_api_key(client: TestClient) -> None:
    response = client.get(
        "/reverse",
        params={"lat": 52.5, "lon": 13.4},
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_load_gadm_raises_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.gpkg"
    with pytest.raises(FileNotFoundError):
        main.load_gadm(str(missing_path), layer="ADM_ADM_4", columns=["geometry"])


def test_load_gadm_logs_and_raises_for_missing_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gpkg_path = tmp_path / "dataset.gpkg"
    gpkg_path.write_bytes(b"")

    def raise_missing_layer(*_args, **_kwargs):
        raise ValueError("Layer not found")

    monkeypatch.setattr(main.gpd, "read_file", raise_missing_layer)

    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError, match="Configured GPKG_LAYER 'ADM_ADM_4' could not be opened"):
            main.load_gadm(str(gpkg_path), layer="ADM_ADM_4", columns=["geometry"])

    assert "Failed to load GeoPackage layer" in caplog.text
    assert str(gpkg_path) in caplog.text
    assert "ADM_ADM_4" in caplog.text


def test_startup_event_populates_state(monkeypatch: pytest.MonkeyPatch, sample_gdf: gpd.GeoDataFrame) -> None:
    main.API_KEY = "test-key"
    monkeypatch.setattr(main, "load_gadm", lambda *_args, **_kwargs: sample_gdf)
    # setup_app_state should initialize settings and the TileCache (no global gdf loaded)
    main.setup_app_state(main.app)
    assert hasattr(main.app.state, "cache")
    assert isinstance(main.app.state.cache, main.TileCache)


def test_settings_from_env_uses_explicit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPKG_FILE", "/tmp/example.gpkg")
    monkeypatch.setenv("GPKG_LAYER", "ADM_ADM_2")
    monkeypatch.setenv("GPKG_CACHE_MODE", "country")
    monkeypatch.setenv("GPKG_CACHE_MAX_TILES", "3")
    monkeypatch.setenv("GPKG_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("API_KEY_HEADER", "X-Auth")

    settings = main.Settings.from_env()

    assert settings.gpkg_file == "/tmp/example.gpkg"
    assert settings.gpkg_layer == "ADM_ADM_2"
    assert settings.gpkg_cache_mode == "country"
    assert settings.gpkg_cache_max_tiles == 3
    assert settings.gpkg_cache_ttl_seconds == 600
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.api_key == "secret"
    assert settings.api_key_header == "X-Auth"






