from __future__ import annotations

from datetime import UTC, datetime

from .weather_runtime import CanonicalWeatherExecution


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_ui_weather_snapshot(
    *,
    canonical_execution: CanonicalWeatherExecution,
) -> dict[str, object]:
    notices: list[str] = []
    try:
        current_speech, current = canonical_execution.build_current_response("")
        current_status = "available"
    except Exception as exc:
        current_speech, current, current_status = "Current weather is unavailable.", {}, "unavailable"
        notices.append(f"Current weather unavailable: {exc}")
    try:
        forecast_speech, forecast = canonical_execution.build_forecast_response("forecast")
        raw_forecast = canonical_execution.fetch_forecast()
        selected_periods = list(raw_forecast.get("periods") or [])[:3]
        forecast_status = "available"
    except Exception as exc:
        forecast_speech, forecast, selected_periods, forecast_status = (
            "Forecast is unavailable.", {}, [], "unavailable"
        )
        notices.append(f"Forecast unavailable: {exc}")
    try:
        solar = canonical_execution.build_solar_snapshot()
        solar_status = "available"
    except Exception as exc:
        solar, solar_status = {}, "unavailable"
        notices.append(f"Solar times unavailable: {exc}")
    try:
        alerts = canonical_execution.fetch_alerts()
        alerts_status = "available"
    except Exception as exc:
        alerts, alerts_status = {"alerts": []}, "unavailable"
        notices.append(f"Weather alerts unavailable: {exc}")
    if all(status == "unavailable" for status in (current_status, forecast_status, solar_status)):
        raise RuntimeError("Oracle could not load current weather, forecast, or solar times.")
    return serialize_ui_weather_snapshot(
        current_speech=current_speech,
        current=current,
        forecast_speech=forecast_speech,
        forecast=forecast,
        selected_periods=selected_periods,
        solar=solar,
        alerts=alerts,
        component_status={
            "current": current_status,
            "forecast": forecast_status,
            "solar": solar_status,
            "alerts": alerts_status,
        },
        notices=notices,
    )


def serialize_ui_weather_snapshot(
    *,
    current_speech: str,
    current: dict[str, object],
    forecast_speech: str,
    forecast: dict[str, object],
    selected_periods: list[object],
    solar: dict[str, object] | None = None,
    alerts: dict[str, object] | None = None,
    component_status: dict[str, str] | None = None,
    notices: list[str] | None = None,
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
        "solar": solar or {},
        "alerts": alerts or {"alerts": []},
        "components": component_status or {"current": "available", "forecast": "available"},
        "location": current.get("location") or forecast.get("location"),
        "state": forecast.get("state"),
        "refresh_after_seconds": 300,
        "notice": " ".join(notices or []) or None,
    }
