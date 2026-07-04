from fastapi import FastAPI, Query
import geopandas as gpd
from shapely.geometry import Point
import os
import time
import uvicorn

app = FastAPI()
FILE = "gadm41_DEU.gpkg"

print("🚀 Booting local GADM geocoding engine...")
t0 = time.perf_counter()

if os.path.exists(FILE):
    print(f"Loading layer 'ADM_ADM_4' from {FILE} into RAM...")
    gdf = gpd.read_file(FILE, layer="ADM_ADM_4")
    print(f"Loaded {len(gdf)} shapes in {time.perf_counter()-t0:.2f}s")
    
    print("Building spatial index cache...")
    spatial_index = gdf.sindex
    print("Engine fully ready to serve requests.")
else:
    raise FileNotFoundError(f"Missing {FILE}! Place your GADM geopackage file in this directory.")

@app.get("/reverse")
def reverse_geocode(lat: float = Query(...), lon: float = Query(...)):
    # Remember: Shapely demands (Longitude, Latitude) layout
    gps_point = Point(lon, lat)
    
    # 1. High-speed bounding box intersection using the spatial index
    candidates = list(spatial_index.intersection(gps_point.bounds))
    possible_matches = gdf.iloc[candidates]
    
    # 2. Pinpoint exact geometry intersection inside the filtered subset
    exact_match = possible_matches[possible_matches.contains(gps_point)]
    
    country_name = "Unknown"
    city_name = "Unknown"
    
    if not exact_match.empty:
        row = exact_match.iloc[0]
        # Pull values straight from your verified column keys
        country_name = row.get("COUNTRY", "Germany")
        city_name = row.get("NAME_4", "Unknown")
                
    # Format the payload to mimic exactly what Dawarich expects from Photon
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "country": country_name,
                "city": city_name
            }
        }]
    }

if __name__ == "__main__":
    
    # Spins up the service on port 2322 to mimic local Photon
    uvicorn.run(app, host="0.0.0.0", port=2322)