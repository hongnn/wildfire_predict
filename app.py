import json
import math
import os
import re
from typing import Any
from datetime import date, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="Wildfire Grid Map", layout="wide")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "wildfire_data" / "selected_3_counties" / "mock_grid_5km"
GEOJSON_PATH = DATA_DIR / "grid_5km_polygons.geojson"
POINTS_PATH = DATA_DIR / "grid_5km_points.csv"
WEATHER_PATH = ROOT / "wildfire_data" / "selected_3_counties" / "weather_data_3counties.csv"
COUNTY_BOUNDARIES_PATH = ROOT / "wildfire_data" / "selected_3_counties" / "ca_counties_boundaries.geojson"
FORECAST_DAYS = 7
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "WildfireForecastApp/1.0"
REQUEST_TIMEOUT_SECONDS = 20

RISK_COLORS = {
    "low": [68, 140, 88, 150],
    "medium": [241, 196, 15, 170],
    "high": [192, 57, 43, 190],
}

CALIFORNIA_VIEW = {
    "latitude": 37.25,
    "longitude": -119.8,
    "zoom": 5.2,
}


def get_google_maps_api_key() -> str | None:
    secrets_key = None
    try:
        secrets_key = st.secrets.get("GOOGLE_MAPS_API_KEY")
    except Exception:
        secrets_key = None

    env_key = os.getenv("GOOGLE_MAPS_API_KEY")
    key = secrets_key or env_key
    return key.strip() if isinstance(key, str) and key.strip() else None


def serialize_for_js(value: Any):
    if isinstance(value, dict):
        return {str(k): serialize_for_js(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize_for_js(v) for v in value]
    if isinstance(value, tuple):
        return [serialize_for_js(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def build_google_map_html(payload: dict[str, Any], component_key: str) -> str:
    google_maps_api_key = get_google_maps_api_key()
    if not google_maps_api_key:
        return "<div style='padding:12px;font-family:Arial,sans-serif;'>Missing GOOGLE_MAPS_API_KEY</div>"

    safe_key = re.sub(r"[^0-9A-Za-z_]", "_", component_key)
    callback_name = f"initGoogleMap_{safe_key}"
    payload_json = json.dumps(serialize_for_js(payload), ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body, #map {{
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        font-family: Arial, sans-serif;
      }}
      .gm-style .info-window {{
        max-width: 320px;
        line-height: 1.45;
      }}
      .map-error {{
        padding: 12px;
        color: #8b0000;
        background: #fff4f4;
        border: 1px solid #f1c0c0;
      }}
    </style>
  </head>
  <body>
    <div id="map"></div>
    <script>
      const payload = {payload_json};

      function rgbaToCss(color, defaultAlpha) {{
        if (!Array.isArray(color) || color.length < 3) return 'rgba(80,80,80,' + (defaultAlpha ?? 0.6) + ')';
        const alpha = color.length > 3 ? color[3] / 255 : (defaultAlpha ?? 0.6);
        return `rgba(${{color[0]}}, ${{color[1]}}, ${{color[2]}}, ${{alpha}})`;
      }}

      function addGeoJsonLayer(map, geojson, styleBuilder, infoWindow) {{
        if (!geojson || !geojson.features || !geojson.features.length) return null;
        const layer = new google.maps.Data({{ map }});
        layer.addGeoJson(geojson);
        layer.setStyle(feature => styleBuilder(feature));
        layer.addListener('click', event => {{
          const html = event.feature.getProperty('info_html');
          if (html) {{
            infoWindow.setContent('<div class="info-window">' + html + '</div>');
            infoWindow.setPosition(event.latLng);
            infoWindow.open({{ map }});
          }}
        }});
        return layer;
      }}

      function addCircleMarkers(map, points, infoWindow) {{
        if (!Array.isArray(points)) return;
        points.forEach(point => {{
          const circle = new google.maps.Circle({{
            strokeColor: rgbaToCss(point.stroke_color || point.fill_color, 0.9),
            strokeOpacity: 1,
            strokeWeight: point.stroke_weight || 1,
            fillColor: rgbaToCss(point.fill_color, 0.55),
            fillOpacity: 1,
            map,
            center: {{ lat: point.lat, lng: point.lon }},
            radius: point.radius_meters || 950,
          }});
          circle.addListener('click', event => {{
            if (point.info_html) {{
              infoWindow.setContent('<div class="info-window">' + point.info_html + '</div>');
              infoWindow.setPosition(event.latLng);
              infoWindow.open({{ map }});
            }}
          }});
        }});
      }}

      function addTextMarkers(map, labels) {{
        if (!Array.isArray(labels)) return;
        labels.forEach(labelItem => {{
          new google.maps.Marker({{
            position: {{ lat: labelItem.lat, lng: labelItem.lon }},
            map,
            clickable: false,
            icon: {{
              path: google.maps.SymbolPath.CIRCLE,
              scale: 0,
            }},
            label: {{
              text: labelItem.text,
              color: '#202124',
              fontSize: '12px',
              fontWeight: '700',
            }},
          }});
        }});
      }}

      function renderMap() {{
        if (!window.google || !window.google.maps) {{
          document.body.innerHTML = '<div class="map-error">Google Maps JS 未成功加载，请检查 Maps JavaScript API、计费状态和 Key 限制。</div>';
          return;
        }}

        const map = new google.maps.Map(document.getElementById('map'), {{
          center: {{ lat: payload.view.latitude, lng: payload.view.longitude }},
          zoom: payload.view.zoom,
          mapTypeId: 'roadmap',
          streetViewControl: false,
          mapTypeControl: false,
          fullscreenControl: true,
        }});

        const infoWindow = new google.maps.InfoWindow();

        addGeoJsonLayer(
          map,
          payload.county_geojson,
          feature => {{
            const fillColor = feature.getProperty('fill_color');
            return {{
              fillColor: rgbaToCss(fillColor, 0.58),
              fillOpacity: 1,
              strokeColor: 'rgba(70,70,70,0.95)',
              strokeWeight: 2,
              clickable: true,
            }};
          }},
          infoWindow
        );

        addGeoJsonLayer(
          map,
          payload.grid_geojson,
          feature => {{
            const fillColor = feature.getProperty('fill_color');
            return {{
              fillColor: rgbaToCss(fillColor, 0.5),
              fillOpacity: 1,
              strokeColor: 'rgba(70,70,70,0.55)',
              strokeWeight: 1,
              clickable: true,
            }};
          }},
          infoWindow
        );

        addCircleMarkers(map, payload.points, infoWindow);
        addTextMarkers(map, payload.labels);
      }}

      window.{callback_name} = renderMap;
    </script>
    <script src="https://maps.googleapis.com/maps/api/js?key={google_maps_api_key}&callback={callback_name}" async defer></script>
  </body>
</html>"""


def render_google_map(payload: dict[str, Any], *, height: int, key: str):
    components.html(build_google_map_html(payload, key), height=height, scrolling=False)


def load_geojson():
    with GEOJSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_points():
    return pd.read_csv(POINTS_PATH)


def load_weather():
    return pd.read_csv(WEATHER_PATH)


def load_county_boundaries():
    with COUNTY_BOUNDARIES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def geojson_cache_key():
    return GEOJSON_PATH.stat().st_mtime_ns


def points_cache_key():
    return POINTS_PATH.stat().st_mtime_ns


def weather_cache_key():
    return WEATHER_PATH.stat().st_mtime_ns


def county_boundaries_cache_key():
    return COUNTY_BOUNDARIES_PATH.stat().st_mtime_ns


def nws_request_json(url: str):
    request = Request(
        url,
        headers={
            "User-Agent": NWS_USER_AGENT,
            "Accept": "application/geo+json, application/ld+json;q=0.9, application/json;q=0.8",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


@st.cache_data
def load_geojson_cached(_cache_key: int):
    return load_geojson()


@st.cache_data
def load_points_cached(_cache_key: int):
    return load_points()


@st.cache_data
def load_weather_cached(_cache_key: int):
    return load_weather()


@st.cache_data
def load_county_boundaries_cached(_cache_key: int):
    return load_county_boundaries()


@st.cache_data(ttl=3600)
def load_nws_forecast_periods_cached(latitude: float, longitude: float):
    point_url = f"{NWS_API_BASE}/points/{latitude:.4f},{longitude:.4f}"
    point_data = nws_request_json(point_url)
    forecast_url = point_data["properties"]["forecast"]
    forecast_data = nws_request_json(forecast_url)
    return forecast_data["properties"]["periods"]


def add_grid_colors(features):
    for feature in features:
        risk_level = feature["properties"]["risk_level"]
        feature["properties"]["fill_color"] = RISK_COLORS[risk_level]
    return features


def county_probability_color(probability_pct: float) -> list[int]:
    if probability_pct >= 30:
        return [192, 57, 43, 170]
    if probability_pct >= 15:
        return [230, 126, 34, 155]
    if probability_pct >= 5:
        return [241, 196, 15, 145]
    return [46, 125, 50, 135]


def county_probability_band(probability_pct: float) -> str:
    if probability_pct >= 30:
        return "Very High"
    if probability_pct >= 15:
        return "High"
    if probability_pct >= 5:
        return "Medium"
    return "Low"


def risk_level_from_score(score: float) -> str:
    if score >= 0.56:
        return "high"
    if score >= 0.34:
        return "medium"
    return "low"


def make_forecast_date_options(anchor_date: date) -> list[date]:
    start_date = anchor_date + timedelta(days=1)
    return [start_date + timedelta(days=offset) for offset in range(FORECAST_DAYS)]


def format_forecast_date(value: date) -> str:
    return f"{value:%Y-%m-%d} {WEEKDAY_LABELS[value.weekday()]}"


def parse_wind_speed_mph(value) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip().lower()
    if not text or text == "nan":
        return None
    if "calm" in text:
        return 0.0
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    if len(numbers) >= 2 and any(token in text for token in ("to", "-", "and")):
        return sum(numbers[:2]) / 2.0
    return numbers[0]


@st.cache_data
def build_geojson_index(geojson_data):
    grouped = {}
    for county_name in {
        feature["properties"]["county"] for feature in geojson_data["features"]
    }:
        county_features = [
            feature
            for feature in geojson_data["features"]
            if feature["properties"]["county"] == county_name
        ]
        grouped[county_name] = {"type": "FeatureCollection", "features": county_features}
    return grouped


@st.cache_data
def build_monthly_weather_profiles(weather_df: pd.DataFrame) -> pd.DataFrame:
    profiles = weather_df.copy()
    profiles["month_num"] = pd.to_datetime(profiles["date"]).dt.month
    return profiles.groupby(["county", "month_num"], as_index=False).agg(
        seasonal_avgtempF=("avgtempF", "mean"),
        seasonal_humid=("humid", "mean"),
        seasonal_wind=("wind", "mean"),
        seasonal_precip=("precip", "mean"),
        seasonal_sunHour=("sunHour", "mean"),
    )


def build_month_baseline(weather_profiles_df: pd.DataFrame, selected_date: date) -> pd.DataFrame:
    month_df = weather_profiles_df[weather_profiles_df["month_num"] == selected_date.month].copy()
    if month_df.empty:
        month_df = (
            weather_profiles_df.groupby("county", as_index=False)
            .agg(
                seasonal_avgtempF=("seasonal_avgtempF", "mean"),
                seasonal_humid=("seasonal_humid", "mean"),
                seasonal_wind=("seasonal_wind", "mean"),
                seasonal_precip=("seasonal_precip", "mean"),
                seasonal_sunHour=("seasonal_sunHour", "mean"),
            )
        )
    return month_df[
        [
            "county",
            "seasonal_avgtempF",
            "seasonal_humid",
            "seasonal_wind",
            "seasonal_precip",
            "seasonal_sunHour",
        ]
    ].copy()


def summarize_nws_forecast(periods, selected_date: date):
    rows = []
    for period in periods:
        start_time = pd.to_datetime(period.get("startTime"), utc=False, errors="coerce")
        if pd.isna(start_time):
            continue
        rows.append(
            {
                "forecast_date": start_time.date(),
                "temperature": float(period["temperature"])
                if period.get("temperature") is not None
                else np.nan,
                "wind_mph": parse_wind_speed_mph(period.get("windSpeed")),
                "pop_pct": float(
                    (period.get("probabilityOfPrecipitation") or {}).get("value") or 0.0
                ),
                "shortForecast": period.get("shortForecast") or "",
            }
        )

    if not rows:
        return None

    forecast_df = pd.DataFrame(rows)
    selected_df = forecast_df[forecast_df["forecast_date"] == selected_date]
    if selected_df.empty:
        return None

    summary_text = " / ".join(
        dict.fromkeys(
            text for text in selected_df["shortForecast"].tolist() if text
        )
    )
    return {
        "forecast_avgtempF": float(selected_df["temperature"].mean()),
        "forecast_wind": float(selected_df["wind_mph"].dropna().mean())
        if selected_df["wind_mph"].notna().any()
        else np.nan,
        "forecast_precip": float(selected_df["pop_pct"].max()) / 100.0,
        "forecast_summary": summary_text or "NWS forecast",
    }


def filter_grid_geojson(geojson_data, point_lookup, county_name, min_score, max_score):
    filtered = []
    for feature in geojson_data["features"]:
        props = feature["properties"]
        if props["county"] != county_name:
            continue
        point_props = point_lookup.get(props["grid_id"])
        if not point_props:
            continue
        risk_score = float(point_props["risk_score"])
        if not (min_score <= risk_score <= max_score):
            continue
        updated_feature = {
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                **props,
                **point_props,
                "fill_color": RISK_COLORS[point_props["risk_level"]],
            },
        }
        filtered.append(updated_feature)
    return {"type": "FeatureCollection", "features": add_grid_colors(filtered)}


def build_county_baseline(points_df: pd.DataFrame) -> pd.DataFrame:
    summary = points_df.groupby("county", as_index=False).agg(
        geoid=("geoid", "first"),
        center_lat=("center_lat", "mean"),
        center_lon=("center_lon", "mean"),
        grid_count=("grid_id", "count"),
        avg_risk_score=("risk_score", "mean"),
        baseline_fire_probability_pct=("monthly_fire_probability_pct", "first"),
        county_avgtempF=("county_avgtempF", "first"),
        county_humid=("county_humid", "first"),
        county_wind=("county_wind", "first"),
        county_precip=("county_precip", "first"),
        county_sunHour=("county_sunHour", "first"),
    )
    summary["label"] = summary["county"].str.replace(" County", "", regex=False)
    return summary


def build_daily_county_summary(
    points_df: pd.DataFrame,
    weather_profiles_df: pd.DataFrame,
    selected_date: date,
    forecast_start: date,
) -> pd.DataFrame:
    summary = build_county_baseline(points_df)
    seasonal_df = build_month_baseline(weather_profiles_df, selected_date)
    summary = summary.merge(seasonal_df, on="county", how="left")

    forecast_rows = []
    for _, row in summary.iterrows():
        try:
            periods = load_nws_forecast_periods_cached(
                round(float(row["center_lat"]), 4),
                round(float(row["center_lon"]), 4),
            )
            forecast_data = summarize_nws_forecast(periods, selected_date)
        except (URLError, TimeoutError, KeyError, ValueError, TypeError):
            forecast_data = None

        if forecast_data is None:
            forecast_data = {
                "forecast_avgtempF": float(row["seasonal_avgtempF"]),
                "forecast_wind": float(row["seasonal_wind"]),
                "forecast_precip": float(row["seasonal_precip"]),
                "forecast_summary": "Seasonal fallback",
            }

        forecast_rows.append(
            {
                "county": row["county"],
                "forecast_avgtempF": forecast_data["forecast_avgtempF"],
                "forecast_wind": forecast_data["forecast_wind"],
                "forecast_precip": forecast_data["forecast_precip"],
                "forecast_summary": forecast_data["forecast_summary"],
            }
        )

    forecast_df = pd.DataFrame(forecast_rows)
    summary = summary.merge(forecast_df, on="county", how="left")

    days_out = max((selected_date - forecast_start).days, 0)
    summary["forecast_humid"] = (
        summary["seasonal_humid"]
        - (summary["forecast_precip"].fillna(summary["seasonal_precip"]) * 24.0)
        + ((summary["forecast_avgtempF"] - summary["seasonal_avgtempF"]) * -0.35)
        - ((summary["forecast_wind"].fillna(summary["seasonal_wind"]) - summary["seasonal_wind"]) * 1.1)
    ).clip(lower=10.0, upper=95.0)
    summary["forecast_sunHour"] = (
        summary["seasonal_sunHour"]
        - (summary["forecast_precip"].fillna(summary["seasonal_precip"]) * 2.0)
    ).clip(lower=0.0)

    temp_signal = (summary["forecast_avgtempF"] - summary["seasonal_avgtempF"]) / 18.0
    humid_signal = (summary["seasonal_humid"] - summary["forecast_humid"]) / 22.0
    wind_signal = (summary["forecast_wind"].fillna(summary["seasonal_wind"]) - summary["seasonal_wind"]) / 3.5
    precip_signal = (summary["seasonal_precip"] - summary["forecast_precip"].fillna(summary["seasonal_precip"])) / 0.15
    sun_signal = (summary["forecast_sunHour"] - summary["seasonal_sunHour"]) / 3.0

    weather_signal = (
        temp_signal * 0.32
        + humid_signal * 0.27
        + wind_signal * 0.19
        + precip_signal * 0.12
        + sun_signal * 0.10
    )
    trend_signal = (days_out - (FORECAST_DAYS - 1) / 2) * 0.12

    summary["daily_fire_probability_pct"] = (
        summary["baseline_fire_probability_pct"] * (1 + weather_signal * 0.72) + trend_signal
    ).clip(lower=0.15, upper=95.0)
    summary["probability_delta_pct"] = (
        summary["daily_fire_probability_pct"] - summary["baseline_fire_probability_pct"]
    )

    summary["forecast_date"] = selected_date.isoformat()
    summary["forecast_source"] = summary["forecast_summary"].apply(
        lambda text: "NWS" if text != "Seasonal fallback" else "Seasonal fallback"
    )
    summary["probability_fill_color"] = summary["daily_fire_probability_pct"].apply(
        county_probability_color
    )
    summary["probability_band"] = summary["daily_fire_probability_pct"].apply(
        county_probability_band
    )
    summary = summary.sort_values("daily_fire_probability_pct", ascending=False).reset_index(drop=True)
    summary["rank"] = range(1, len(summary) + 1)
    summary["probability_label"] = summary.apply(
        lambda row: f"{row['label']}\n{row['daily_fire_probability_pct']:.1f}%",
        axis=1,
    )
    return summary.round(
        {
            "avg_risk_score": 4,
            "baseline_fire_probability_pct": 2,
            "daily_fire_probability_pct": 2,
            "probability_delta_pct": 2,
            "forecast_avgtempF": 2,
            "forecast_humid": 2,
            "forecast_wind": 2,
            "forecast_precip": 4,
            "forecast_sunHour": 2,
        }
    )


def build_forecast_points(points_df: pd.DataFrame, county_df: pd.DataFrame) -> pd.DataFrame:
    forecast_points = points_df.copy().merge(
        county_df[
            [
                "county",
                "forecast_date",
                "daily_fire_probability_pct",
                "probability_delta_pct",
                "forecast_avgtempF",
                "forecast_humid",
                "forecast_wind",
                "forecast_precip",
                "forecast_sunHour",
                "forecast_source",
                "forecast_summary",
            ]
        ],
        on="county",
        how="left",
    )

    base_prob = forecast_points["monthly_fire_probability_pct"].clip(lower=0.1)
    county_ratio = forecast_points["daily_fire_probability_pct"] / base_prob
    local_heat = (forecast_points["mock_avgtempF"] - forecast_points["county_avgtempF"]) / 12.0
    local_dryness = (forecast_points["county_humid"] - forecast_points["mock_humid"]) / 20.0
    wind_shift = (forecast_points["forecast_wind"] - forecast_points["county_wind"]) / 18.0
    sun_shift = (forecast_points["forecast_sunHour"] - forecast_points["county_sunHour"]) / 25.0

    forecast_points["risk_score"] = (
        forecast_points["risk_score"]
        + (county_ratio - 1.0) * 0.14
        + local_heat * 0.05
        + local_dryness * 0.05
        + wind_shift * 0.04
        + sun_shift * 0.02
    ).clip(lower=0.01, upper=0.99)
    forecast_points["risk_level"] = forecast_points["risk_score"].apply(risk_level_from_score)

    return forecast_points.round(
        {
            "risk_score": 4,
            "daily_fire_probability_pct": 2,
            "probability_delta_pct": 2,
            "forecast_avgtempF": 2,
            "forecast_humid": 2,
            "forecast_wind": 2,
            "forecast_precip": 4,
            "forecast_sunHour": 2,
        }
    )


def merge_dynamic_risk_into_counties(
    county_df: pd.DataFrame, forecast_points_df: pd.DataFrame
) -> pd.DataFrame:
    risk_summary = forecast_points_df.groupby("county", as_index=False).agg(
        avg_risk_score=("risk_score", "mean")
    )
    merged = county_df.drop(columns=["avg_risk_score"]).merge(risk_summary, on="county", how="left")
    return merged.round({"avg_risk_score": 4})


def build_county_overview_geojson(county_boundaries, county_df: pd.DataFrame):
    county_lookup = county_df.set_index("county").to_dict("index")
    features = []
    for feature in county_boundaries["features"]:
        county_name = f"{feature['properties']['name']} County"
        county_stats = county_lookup.get(county_name)
        if not county_stats:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "county": county_name,
                    "label": county_stats["label"],
                    "forecast_date": county_stats["forecast_date"],
                    "grid_count": int(county_stats["grid_count"]),
                    "avg_risk_score": round(float(county_stats["avg_risk_score"]), 4),
                    "baseline_fire_probability_pct": round(
                        float(county_stats["baseline_fire_probability_pct"]), 2
                    ),
                    "daily_fire_probability_pct": round(
                        float(county_stats["daily_fire_probability_pct"]), 2
                    ),
                    "probability_delta_pct": round(
                        float(county_stats["probability_delta_pct"]), 2
                    ),
                    "forecast_summary": county_stats["forecast_summary"],
                    "forecast_source": county_stats["forecast_source"],
                    "probability_band": county_stats["probability_band"],
                    "fill_color": county_stats["probability_fill_color"],
                },
                "geometry": feature["geometry"],
            }
        )
    return {"type": "FeatureCollection", "features": features}


def compute_county_view_state(df: pd.DataFrame) -> dict[str, float]:
    lat_min = float(df["center_lat"].min())
    lat_max = float(df["center_lat"].max())
    lon_min = float(df["center_lon"].min())
    lon_max = float(df["center_lon"].max())
    lat_span = max(lat_max - lat_min, 0.08)
    lon_span = max(lon_max - lon_min, 0.08)
    span = max(lat_span, lon_span)

    if span > 3.0:
        zoom = 6.2
    elif span > 2.0:
        zoom = 6.8
    elif span > 1.2:
        zoom = 7.3
    else:
        zoom = 8.0

    return {
        "latitude": float(df["center_lat"].mean()),
        "longitude": float(df["center_lon"].mean()),
        "zoom": zoom,
    }


def render_overview_map(county_df: pd.DataFrame, county_overview_geojson):
    features = []
    for feature in county_overview_geojson["features"]:
        props = feature["properties"].copy()
        props["info_html"] = (
            f"<b>{props['county']}</b><br/>"
            f"Forecast date: {props['forecast_date']}<br/>"
            f"Weather source: {props['forecast_source']}<br/>"
            f"Forecast: {props['forecast_summary']}<br/>"
            f"Probability band: {props['probability_band']}<br/>"
            f"Daily fire probability: {props['daily_fire_probability_pct']}%<br/>"
            f"Change vs baseline: {props['probability_delta_pct']} pp<br/>"
            f"Grid cells: {props['grid_count']}<br/>"
            f"Avg risk score: {props['avg_risk_score']}"
        )
        features.append(
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": props,
            }
        )

    labels = [
        {
            "lat": float(row["center_lat"]),
            "lon": float(row["center_lon"]),
            "text": f"{row['label']} {row['daily_fire_probability_pct']:.1f}%",
        }
        for _, row in county_df.iterrows()
    ]

    render_google_map(
        {
            "view": CALIFORNIA_VIEW,
            "county_geojson": {"type": "FeatureCollection", "features": features},
            "labels": labels,
            "points": [],
            "grid_geojson": None,
        },
        height=580,
        key="county_overview_map",
    )


def render_detail_map(
    points_df: pd.DataFrame,
    geojson_by_county,
    county_name: str,
    min_score: float,
    max_score: float,
    render_mode: str,
):
    filtered_points = points_df[
        (points_df["county"] == county_name)
        & (points_df["risk_score"].between(min_score, max_score))
    ].copy()

    if filtered_points.empty:
        st.warning("当前筛选条件下，这个县没有可显示的网格。")
        return

    filtered_points["point_color"] = filtered_points["risk_level"].map(RISK_COLORS)
    county_geojson = geojson_by_county[county_name]
    point_lookup = filtered_points.set_index("grid_id")[
        [
            "forecast_date",
            "risk_score",
            "risk_level",
            "daily_fire_probability_pct",
            "probability_delta_pct",
            "forecast_avgtempF",
            "forecast_humid",
            "forecast_wind",
            "forecast_source",
            "forecast_summary",
        ]
    ].to_dict("index")
    filtered_geojson = filter_grid_geojson(
        county_geojson, point_lookup, county_name, min_score, max_score
    )
    grid_features = []
    for feature in filtered_geojson["features"]:
        props = feature["properties"].copy()
        if render_mode == "Fast points":
            props["fill_color"] = [255, 255, 255, 0]
        props["info_html"] = (
            f"<b>{props['county']}</b><br/>"
            f"Forecast date: {props['forecast_date']}<br/>"
            f"Weather source: {props['forecast_source']}<br/>"
            f"Forecast: {props['forecast_summary']}<br/>"
            f"Grid: {props['grid_id']}<br/>"
            f"Risk: {props['risk_level']} ({props['risk_score']})<br/>"
            f"Daily fire probability: {props['daily_fire_probability_pct']}%<br/>"
            f"Change vs baseline: {props['probability_delta_pct']} pp<br/>"
            f"Forecast temp(F): {props['forecast_avgtempF']}<br/>"
            f"Forecast humidity: {props['forecast_humid']}<br/>"
            f"Forecast wind: {props['forecast_wind']}"
        )
        grid_features.append(
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": props,
            }
        )
    grid_geojson = {"type": "FeatureCollection", "features": grid_features}

    point_records = []
    for _, row in filtered_points.iterrows():
        point_records.append(
            {
                "lat": float(row["center_lat"]),
                "lon": float(row["center_lon"]),
                "radius_meters": 950,
                "fill_color": row["point_color"],
                "stroke_color": [30, 30, 30, 160],
                "stroke_weight": 1,
                "info_html": (
                    f"<b>{row['county']}</b><br/>"
                    f"Forecast date: {row['forecast_date']}<br/>"
                    f"Weather source: {row['forecast_source']}<br/>"
                    f"Forecast: {row['forecast_summary']}<br/>"
                    f"Grid: {row['grid_id']}<br/>"
                    f"Risk: {row['risk_level']} ({row['risk_score']})<br/>"
                    f"Daily fire probability: {row['daily_fire_probability_pct']}%<br/>"
                    f"Change vs baseline: {row['probability_delta_pct']} pp<br/>"
                    f"Forecast temp(F): {row['forecast_avgtempF']}<br/>"
                    f"Forecast humidity: {row['forecast_humid']}<br/>"
                    f"Forecast wind: {row['forecast_wind']}"
                ),
            }
        )

    render_google_map(
        {
            "view": compute_county_view_state(filtered_points),
            "county_geojson": None,
            "grid_geojson": grid_geojson,
            "labels": [],
            "points": point_records,
        },
        height=620,
        key="county_detail_map",
    )

    summary = (
        filtered_points.groupby("risk_level", as_index=False)
        .agg(
            grid_count=("grid_id", "count"),
            avg_risk_score=("risk_score", "mean"),
        )
        .sort_values("avg_risk_score", ascending=False)
        .round({"avg_risk_score": 4})
    )

    top_rows = filtered_points.sort_values("risk_score", ascending=False).head(20)

    left, right = st.columns([1, 1.2])
    with left:
        st.subheader("Risk Summary")
        st.dataframe(summary, use_container_width=True, hide_index=True)
    with right:
        st.subheader("Top Grid Cells")
        st.dataframe(
            top_rows[
                [
                    "grid_id",
                    "risk_level",
                    "risk_score",
                    "daily_fire_probability_pct",
                    "probability_delta_pct",
                    "center_lat",
                    "center_lon",
                ]
            ].rename(
                columns={
                    "daily_fire_probability_pct": "daily_fire_probability_pct(%)",
                    "probability_delta_pct": "delta_vs_baseline(pp)",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


def main():
    st.title("California Wildfire County Map")
    st.caption("支持切换未来 7 天日期，县级概率会参考 NWS 官方天气预报动态变化。")

    if get_google_maps_api_key():
        st.caption("当前地图底图：Google Maps")
    else:
        st.caption("当前地图底图：未配置 Google Maps，请先设置 GOOGLE_MAPS_API_KEY")

    if (
        not GEOJSON_PATH.exists()
        or not POINTS_PATH.exists()
        or not WEATHER_PATH.exists()
    ):
        st.error("预测数据缺失，请先确认网格和天气数据文件存在。")
        return

    geojson_data = load_geojson_cached(geojson_cache_key())
    geojson_by_county = build_geojson_index(geojson_data)
    points_df = load_points_cached(points_cache_key())
    weather_df = load_weather_cached(weather_cache_key())
    weather_profiles_df = build_monthly_weather_profiles(weather_df)
    county_boundaries = load_county_boundaries_cached(county_boundaries_cache_key())

    forecast_dates = make_forecast_date_options(date.today())

    with st.sidebar:
        st.header("Controls")
        selected_date = st.selectbox(
            "Forecast date",
            options=forecast_dates,
            index=0,
            format_func=format_forecast_date,
        )
        st.caption(
            f"未来一周可选范围：{forecast_dates[0]:%Y-%m-%d} 至 {forecast_dates[-1]:%Y-%m-%d}"
        )

    county_df = build_daily_county_summary(
        points_df,
        weather_profiles_df,
        selected_date,
        forecast_dates[0],
    )
    forecast_points_df = build_forecast_points(points_df, county_df)
    county_df = merge_dynamic_risk_into_counties(county_df, forecast_points_df)
    county_overview_geojson = build_county_overview_geojson(county_boundaries, county_df)

    if "selected_county" not in st.session_state:
        st.session_state["selected_county"] = None

    with st.sidebar:
        min_score, max_score = st.slider(
            "Risk score range",
            min_value=0.0,
            max_value=1.0,
            value=(0.0, 1.0),
            step=0.01,
        )
        render_mode = st.radio(
            "County view mode",
            options=["Fast points", "Grid polygons"],
            index=1,
        )

        county_names = county_df["county"].tolist()
        current_county = st.session_state["selected_county"]
        county_index = 0
        if current_county in county_names:
            county_index = county_names.index(current_county) + 1

        selected_from_box = st.selectbox(
            "County",
            options=["California Overview"] + county_names,
            index=county_index,
        )

        if selected_from_box == "California Overview":
            if st.button("Reset To Overview", use_container_width=True):
                st.session_state["selected_county"] = None
                st.rerun()
        else:
            if selected_from_box != st.session_state["selected_county"]:
                st.session_state["selected_county"] = selected_from_box
                st.rerun()
            if st.button("Back To California", use_container_width=True):
                st.session_state["selected_county"] = None
                st.rerun()

        st.markdown("颜色说明：`low` 绿色，`medium` 黄色，`high` 红色")

    selected_county = st.session_state["selected_county"]
    selected_date_text = format_forecast_date(selected_date)
    st.write(f"当前预测日期：`{selected_date_text}`")

    if selected_county is None:
        st.subheader("California Overview")
        st.write("县面颜色和标注显示所选日期的县级起火概率；这些概率会参考未来天气预报变化。请使用左侧 County 选择器切换到县级 5km 网格视图。")
        top_cols = st.columns(len(county_df))
        for idx, (_, row) in enumerate(county_df.iterrows()):
            top_cols[idx].metric(
                f"#{int(row['rank'])} {row['label']}",
                f"{row['daily_fire_probability_pct']:.2f}%",
                f"{row['probability_delta_pct']:+.2f} pp",
            )
        render_overview_map(county_df, county_overview_geojson)
        st.dataframe(
            county_df[
                [
                    "rank",
                    "county",
                    "forecast_date",
                    "probability_band",
                    "daily_fire_probability_pct",
                    "probability_delta_pct",
                    "grid_count",
                    "avg_risk_score",
                ]
            ].rename(
                columns={
                    "forecast_date": "forecast_date",
                    "daily_fire_probability_pct": "daily_fire_probability_pct(%)",
                    "probability_delta_pct": "delta_vs_baseline(pp)",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        selected_row = county_df[county_df["county"] == selected_county].iloc[0]
        st.subheader(selected_county)
        metric_cols = st.columns(4)
        metric_cols[0].metric(
            "预测起火概率",
            f"{selected_row['daily_fire_probability_pct']:.2f}%",
            f"{selected_row['probability_delta_pct']:+.2f} pp",
        )
        metric_cols[1].metric("县平均风险分", f"{selected_row['avg_risk_score']:.4f}")
        metric_cols[2].metric("预测温度", f"{selected_row['forecast_avgtempF']:.1f} F")
        metric_cols[3].metric("预测湿度", f"{selected_row['forecast_humid']:.1f}%")
        st.write("当前视图显示所选日期下该县内的 5km 预测结果，日期变化会同步影响县级概率和网格风险分布。")
        render_detail_map(
            forecast_points_df,
            geojson_by_county,
            selected_county,
            min_score,
            max_score,
            render_mode,
        )


if __name__ == "__main__":
    main()
