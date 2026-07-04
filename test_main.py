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


def test_reverse_geocode_endpoint_supports_radius(client: TestClient) -> None:
    response = client.get(
        "/reverse",
        params={"lat": 52.5, "lon": 13.4, "radius": 10},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["features"][0]["properties"]["country"] == "Germany"


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
