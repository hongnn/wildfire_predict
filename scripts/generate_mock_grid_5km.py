import csv
import json
import math
import os
import time
from collections import defaultdict
from urllib.request import urlopen

import requests
from shapely.geometry import GeometryCollection, box, mapping, shape
from shapely.ops import unary_union


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "wildfire_data", "selected_3_counties")
LOC_PATH = os.path.join(DATA_DIR, "CA_Counties_Location_3counties.csv")
WEATHER_PATH = os.path.join(DATA_DIR, "weather_data_3counties.csv")
NDVI_PATH = os.path.join(DATA_DIR, "ndvi_data_3counties.csv")
FIRE_PATH = os.path.join(DATA_DIR, "fire_data_3counties.csv")
OUT_DIR = os.path.join(DATA_DIR, "mock_grid_5km")
COUNTY_BOUNDARIES_PATH = os.path.join(DATA_DIR, "ca_counties_boundaries.geojson")
COUNTY_BOUNDARIES_URL = (
    "https://raw.githubusercontent.com/codeforamerica/click_that_hood/"
    "master/public/data/california-counties.geojson"
)

GRID_KM = 5.0
GRID_DEG_LAT = GRID_KM / 111.32
TARGET_MONTH = "2020-08"
TARGET_YEAR = 2020
TARGET_MONTH_NUM = 8
BASE_MONTHS = 150.0


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def read_csv(path: str):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def county_seed(name: str) -> int:
    return sum(ord(ch) for ch in name)


def seasonal_bias(lat: float, lon: float) -> float:
    # Stable spatial variation to keep mock values from looking uniform.
    return math.sin(lat * 7.0) * 0.12 + math.cos(lon * 5.0) * 0.10


def ellipse_membership(dx_km: float, dy_km: float, rx_km: float, ry_km: float) -> float:
    return (dx_km * dx_km) / (rx_km * rx_km) + (dy_km * dy_km) / (ry_km * ry_km)


def ensure_county_boundaries_file() -> None:
    if os.path.exists(COUNTY_BOUNDARIES_PATH):
        return

    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(COUNTY_BOUNDARIES_URL, timeout=60)
            response.raise_for_status()
            data = response.content
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        try:
            with urlopen(COUNTY_BOUNDARIES_URL, timeout=60) as response:
                data = response.read()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to download county boundaries from {COUNTY_BOUNDARIES_URL}"
            ) from (last_error or exc)

    with open(COUNTY_BOUNDARIES_PATH, "wb") as f:
        f.write(data)


def polygonal_geometry(geom):
    if geom.is_empty:
        return geom
    if geom.geom_type in {"Polygon", "MultiPolygon"}:
        return geom
    if not hasattr(geom, "geoms"):
        return GeometryCollection()

    polygons = []
    for part in geom.geoms:
        if part.geom_type == "Polygon":
            polygons.append(part)
        elif part.geom_type == "MultiPolygon":
            polygons.extend(list(part.geoms))

    if not polygons:
        return GeometryCollection()
    return unary_union(polygons)


def keep_mainland_polygon(geom):
    if geom.is_empty:
        return geom
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda polygon: polygon.area)
    return geom


def load_county_boundaries():
    ensure_county_boundaries_file()
    with open(COUNTY_BOUNDARIES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    target_names = {"Santa Barbara", "Shasta", "Sacramento"}
    boundaries = {}
    for feature in data["features"]:
        county_name = feature["properties"]["name"]
        if county_name not in target_names:
            continue

        geometry = polygonal_geometry(shape(feature["geometry"]).buffer(0))
        geometry = keep_mainland_polygon(geometry)
        if geometry.is_empty:
            continue
        boundaries[county_name] = geometry

    missing = target_names - set(boundaries)
    if missing:
        raise ValueError(f"Missing county boundaries for: {sorted(missing)}")
    return boundaries


def make_grid_for_county(county_info, county_metrics, county_geometry):
    lat = county_info["lat"]
    lon = county_info["lon"]
    area_km2 = county_info["aland_km2"]
    county = county_info["county"]
    county_full = county_info["county_full"]

    cos_lat = math.cos(math.radians(lat))
    grid_deg_lon = GRID_KM / (111.32 * max(cos_lat, 0.2))

    # Approximate county extent as an ellipse using area and a county-specific aspect ratio.
    aspect_lookup = {
        "Santa Barbara": 2.6,
        "Shasta": 1.45,
        "Sacramento": 1.3,
    }
    aspect = aspect_lookup.get(county, 1.6)
    rx_km = math.sqrt((area_km2 * aspect) / math.pi)
    ry_km = area_km2 / (math.pi * rx_km)

    lon_min, lat_min, lon_max, lat_max = county_geometry.bounds

    county_fire_pct = county_metrics["monthly_fire_probability_pct"]
    county_fire_index = county_metrics["county_fire_index"]
    ndvi = county_metrics["ndvi"]
    temp_f = county_metrics["avgtempF"]
    humid = county_metrics["humid"]
    wind = county_metrics["wind"]
    precip = county_metrics["precip"]
    sun_hour = county_metrics["sunHour"]

    features = []
    points = []
    row = 0
    grid_serial = 1
    current_lat = lat_min + GRID_DEG_LAT / 2.0
    while current_lat <= lat_max:
        current_lon = lon_min + grid_deg_lon / 2.0
        col = 0
        while current_lon <= lon_max:
            half_lat = GRID_DEG_LAT / 2.0
            half_lon = grid_deg_lon / 2.0
            cell_polygon = box(
                current_lon - half_lon,
                current_lat - half_lat,
                current_lon + half_lon,
                current_lat + half_lat,
            )
            clipped = polygonal_geometry(county_geometry.intersection(cell_polygon))

            if not clipped.is_empty:
                rep_point = clipped.representative_point()
                sample_lon = rep_point.x
                sample_lat = rep_point.y
                dx_km = (sample_lon - lon) * 111.32 * cos_lat
                dy_km = (sample_lat - lat) * 111.32
                shape_value = ellipse_membership(dx_km, dy_km, rx_km, ry_km)
                edge_factor = clamp(1.0 - (shape_value ** 0.75), 0.0, 1.0)
                spatial = seasonal_bias(sample_lat, sample_lon)
                county_offset = (county_seed(county) % 19) / 100.0 - 0.09

                risk_score = (
                    county_fire_index * 0.55
                    + (1.0 - ndvi) * 0.16
                    + clamp((temp_f - 70.0) / 35.0, 0.0, 1.0) * 0.14
                    + clamp(wind / 12.0, 0.0, 1.0) * 0.08
                    + clamp(sun_hour / 14.0, 0.0, 1.0) * 0.04
                    - clamp(humid / 100.0, 0.0, 1.0) * 0.06
                    - clamp(precip * 8.0, 0.0, 0.05)
                    + edge_factor * 0.06
                    + spatial * 0.08
                    + county_offset * 0.03
                )
                risk_score = clamp(risk_score, 0.01, 0.99)

                # Use an interior land point so coastal cells do not render in the ocean.
                local_ndvi = clamp(ndvi + spatial * 0.10 - edge_factor * 0.03, 0.05, 0.95)
                local_temp = round(temp_f + spatial * 4.0 + edge_factor * 1.5, 2)
                local_humid = round(
                    clamp(humid - spatial * 9.0 - edge_factor * 6.0, 8.0, 95.0), 2
                )
                local_wind = round(
                    clamp(wind + abs(spatial) * 2.2 + edge_factor * 1.6, 0.3, 25.0), 2
                )

                grid_id = f"{county_info['geoid']}_5km_{grid_serial:04d}"
                point_row = {
                    "grid_id": grid_id,
                    "county": county_full,
                    "county_short": county,
                    "geoid": county_info["geoid"],
                    "grid_km": GRID_KM,
                    "center_lat": round(sample_lat, 6),
                    "center_lon": round(sample_lon, 6),
                    "row_index": row,
                    "col_index": col,
                    "is_mock": 1,
                    "shape_model": "county_boundary_clipped_grid",
                    "month": TARGET_MONTH,
                    "monthly_fire_probability_pct": round(county_fire_pct, 2),
                    "risk_score": round(risk_score, 4),
                    "risk_level": risk_level(risk_score),
                    "mock_ndvi": round(local_ndvi, 4),
                    "mock_avgtempF": local_temp,
                    "mock_humid": local_humid,
                    "mock_wind": local_wind,
                    "county_avgtempF": round(temp_f, 2),
                    "county_humid": round(humid, 2),
                    "county_wind": round(wind, 2),
                    "county_precip": round(precip, 4),
                    "county_sunHour": round(sun_hour, 2),
                }
                points.append(point_row)
                features.append(
                    {
                        "type": "Feature",
                        "properties": point_row,
                        "geometry": mapping(clipped),
                    }
                )
                grid_serial += 1

            col += 1
            current_lon += grid_deg_lon
        row += 1
        current_lat += GRID_DEG_LAT

    return points, features


def risk_level(score: float) -> str:
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "medium"
    return "low"


def load_county_info():
    rows = read_csv(LOC_PATH)
    result = []
    for row in rows:
        county = row["NAME"]
        county_full = f"{county} County"
        result.append(
            {
                "county": county,
                "county_full": county_full,
                "geoid": row["GEOID"],
                "lat": float(str(row["INTPTLAT"]).replace("+", "")),
                "lon": float(row["INTPTLON"]),
                "aland_km2": float(row["ALAND"]) / 1_000_000.0,
            }
        )
    return result


def load_metrics():
    weather_rows = read_csv(WEATHER_PATH)
    ndvi_rows = read_csv(NDVI_PATH)
    fire_rows = read_csv(FIRE_PATH)

    weather_by_county = {}
    for row in weather_rows:
        if row["date"] == TARGET_MONTH:
            weather_by_county[row["county"]] = {
                "avgtempF": float(row["avgtempF"]),
                "humid": float(row["humid"]),
                "wind": float(row["wind"]),
                "precip": float(row["precip"]),
                "sunHour": float(row["sunHour"]),
            }

    ndvi_by_county = {}
    for row in ndvi_rows:
        if int(row["Year"]) == TARGET_YEAR and int(row["Month"]) == TARGET_MONTH_NUM:
            ndvi_by_county[row["county"]] = float(row["ndvi"])

    fire_months_by_county = defaultdict(set)
    for row in fire_rows:
        fire_months_by_county[row["UNIT_ID"]].add(row["ALARM_DATE"])

    metrics = {}
    for county_full in weather_by_county:
        county_short = county_full.replace(" County", "")
        fire_months = len(fire_months_by_county[county_full])
        fire_pct = (fire_months / BASE_MONTHS) * 100.0
        metrics[county_full] = {
            **weather_by_county[county_full],
            "ndvi": ndvi_by_county[county_short],
            "monthly_fire_probability_pct": fire_pct,
            "county_fire_index": clamp(fire_pct / 50.0, 0.02, 0.95),
        }
    return metrics


def write_csv(path: str, rows):
    if not rows:
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_geojson(path: str, features):
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False)


def write_summary(path: str, county_summaries, total_grids: int):
    lines = [
        "# 5km Grid Mock Data",
        "",
        "This dataset is intended for map display and prototyping.",
        "Grid geometry is generated by clipping 5km cells to county boundary polygons.",
        "For coastal counties, only the largest mainland polygon is kept so offshore islands are excluded.",
        "The geometry is suitable for map prototyping, but it is not a legal or survey-grade boundary product.",
        "",
        f"Target month used for weather/NDVI baseline: `{TARGET_MONTH}`",
        f"Grid size: `{GRID_KM} km`",
        f"Total grid cells: `{total_grids}`",
        "",
        "Counties:",
    ]
    for summary in county_summaries:
        lines.append(
            f"- {summary['county']}: {summary['grid_count']} grids, "
            f"county monthly fire probability {summary['monthly_fire_probability_pct']:.2f}%"
        )
    lines.extend(
        [
            "",
            "Outputs:",
            "- `grid_5km_points.csv`: grid center points and mock attributes",
            "- `grid_5km_polygons.geojson`: 5km grid polygons for direct map rendering",
            f"- `../{os.path.basename(COUNTY_BOUNDARIES_PATH)}`: cached county boundary source",
            "",
            "Important fields:",
            "- `risk_score`: 0-1 mock wildfire risk score for map coloring",
            "- `risk_level`: low / medium / high bucket",
            "- `monthly_fire_probability_pct`: county-level fire-month ratio from the original fire table",
            "- `shape_model`: documents that this mock now uses mainland county-boundary clipping",
        ]
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ensure_dir(OUT_DIR)
    county_info_list = load_county_info()
    metrics = load_metrics()
    county_boundaries = load_county_boundaries()

    all_points = []
    all_features = []
    county_summaries = []

    for county_info in county_info_list:
        county_full = county_info["county_full"]
        county_metrics = metrics[county_full]
        county_geometry = county_boundaries[county_info["county"]]
        points, features = make_grid_for_county(
            county_info, county_metrics, county_geometry
        )
        all_points.extend(points)
        all_features.extend(features)
        county_summaries.append(
            {
                "county": county_full,
                "grid_count": len(points),
                "monthly_fire_probability_pct": county_metrics["monthly_fire_probability_pct"],
            }
        )

    write_csv(os.path.join(OUT_DIR, "grid_5km_points.csv"), all_points)
    write_geojson(os.path.join(OUT_DIR, "grid_5km_polygons.geojson"), all_features)
    write_summary(
        os.path.join(OUT_DIR, "README.md"),
        county_summaries,
        total_grids=len(all_points),
    )

    print(f"Generated {len(all_points)} grid cells.")
    for summary in county_summaries:
        print(
            f"{summary['county']}: {summary['grid_count']} grids, "
            f"monthly_fire_probability_pct={summary['monthly_fire_probability_pct']:.2f}"
        )


if __name__ == "__main__":
    main()
