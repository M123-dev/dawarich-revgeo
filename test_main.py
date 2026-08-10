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
    main.startup_event()
    return TestClient(main.app)


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
    app.state.cache = main.CountryCache(max_entries=3, ttl_seconds=900)
    app.state.cache.put("Germany", cached_gdf)
    app.state.gdf = fresh_gdf
    app.state.spatial_index = fresh_gdf.sindex
    app.state.active_country = "Germany"

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


def test_startup_event_populates_state(monkeypatch: pytest.MonkeyPatch, sample_gdf: gpd.GeoDataFrame) -> None:
    main.API_KEY = "test-key"
    monkeypatch.setattr(main, "load_gadm", lambda *_args, **_kwargs: sample_gdf)
    main.startup_event()

    assert hasattr(main.app.state, "gdf")
    assert hasattr(main.app.state, "spatial_index")
    assert main.app.state.gdf is sample_gdf


def test_settings_from_env_uses_explicit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPKG_FILE", "/tmp/example.gpkg")
    monkeypatch.setenv("GPKG_LAYER", "ADM_ADM_2")
    monkeypatch.setenv("GPKG_CACHE_MODE", "country")
    monkeypatch.setenv("GPKG_CACHE_MAX_COUNTRIES", "3")
    monkeypatch.setenv("GPKG_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("API_KEY_HEADER", "X-Auth")

    settings = main.Settings.from_env()

    assert settings.gpkg_file == "/tmp/example.gpkg"
    assert settings.gpkg_layer == "ADM_ADM_2"
    assert settings.gpkg_cache_mode == "country"
    assert settings.gpkg_cache_max_countries == 3
    assert settings.gpkg_cache_ttl_seconds == 600
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.api_key == "secret"
    assert settings.api_key_header == "X-Auth"


def test_settings_resolves_relative_dataset_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPKG_FILE", "datasets/region.gpkg")

    settings = main.Settings.from_env()

    assert settings.gpkg_path == (tmp_path / "datasets/region.gpkg").resolve()
