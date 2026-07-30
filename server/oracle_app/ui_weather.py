from __future__ import annotations

from datetime import UTC, datetime

from .weather_runtime import CanonicalWeatherExecution


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_ui_weather_snapshot(
    *,
    canonical_execution: CanonicalWeatherExecution,
) -> dict[str, object]:
    current_speech, current = canonical_execution.build_current_response("")
    forecast_speech, forecast = canonical_execution.build_forecast_response("forecast")
    try:
        raw_forecast = canonical_execution.fetch_forecast()
        selected_periods = list(raw_forecast.get("periods") or [])[:3]
    except Exception:
        selected_periods = list(forecast.get("selected_periods") or [])
    return serialize_ui_weather_snapshot(
        current_speech=current_speech,
        current=current,
        forecast_speech=forecast_speech,
        forecast=forecast,
        selected_periods=selected_periods,
    )


def serialize_ui_weather_snapshot(
    *,
    current_speech: str,
    current: dict[str, object],
    forecast_speech: str,
    forecast: dict[str, object],
    selected_periods: list[object],
) -> dict[str, object]:
    return {
        "ok": True,
        "generated_at": _build_ui_generated_at(),
        "current": {
            "summary": current_speech,
            "temperature_f": current.get("temperature_f"),
            "dewpoint_f": current.get("dewpoint_f"),
            "freshness_class": current.get("freshness_class"),
            "observation_timestamp": current.get("observation_timestamp"),
            "humidity_pct": current.get("humidity_pct"),
            "heat_index_f": current.get("heat_index_f"),
            "wind_speed_mph": current.get("wind_speed_mph"),
            "rain_rate_in_h": current.get("rain_rate_in_h"),
            "age_seconds": current.get("age_seconds"),
            "barometer_inhg": current.get("barometer_inhg"),
            "wind_gust_mph": current.get("wind_gust_mph"),
            "wind_chill_f": current.get("wind_chill_f"),
            "wind_direction_cardinal": current.get("wind_direction_cardinal"),
            "inside_temperature_f": current.get("inside_temperature_f"),
            "inside_humidity_pct": current.get("inside_humidity_pct"),
            "rain_total_in": current.get("rain_total_in"),
            "source_name": current.get("source_name"),
        },
        "forecast": {
            "summary": forecast_speech,
            "periods": selected_periods,
        },
        "location": current.get("location") or forecast.get("location"),
        "state": forecast.get("state"),
        "refresh_after_seconds": 300,
    }
