from __future__ import annotations

from datetime import date, datetime, timedelta
from types import MappingProxyType
from unittest.mock import Mock

import pytest

from oracle_app.configuration.weather_runtime_settings import (
    SolarWeatherLocationSettings,
    SolarWeatherRuntimeSettings,
)
from oracle_app.provider_bridges.remote_weather import NominatimNwsRemoteWeatherBridge
from oracle_app.schemas import DispatchPlan
from oracle_app.handlers.weather import WeatherHandler
from oracle_app.session_state import clear_all_sessions, get_informational_context, get_pending_state
from oracle_app.ui_weather import build_ui_weather_snapshot
from oracle_app.weather_forecast import format_forecast_summary
from oracle_app.weather_models import ForecastPeriod, ResolvedRemoteLocation
from oracle_app.weather_solar import (
    SolarLocationError,
    SolarNoEventError,
    build_solar_response,
    detect_solar_weather_query,
)


HOME = SolarWeatherLocationSettings(
    id="home",
    label="Dushore",
    aliases=("here", "local"),
    latitude=41.593105419238576,
    longitude=-76.44129162882626,
    timezone="America/New_York",
)
SOLAR = SolarWeatherRuntimeSettings(True, "home", MappingProxyType({"home": HOME}))


def _period(name: str, temp: int, short: str, precipitation: int | None) -> ForecastPeriod:
    start = datetime.fromisoformat("2026-09-07T06:00:00-04:00")
    return ForecastPeriod(
        name=name,
        start_time=start,
        end_time=start + timedelta(hours=12),
        is_daytime=True,
        temperature_f=temp,
        temperature_trend=None,
        wind_speed="5 mph",
        wind_direction="NW",
        short_forecast=short,
        detailed_forecast=short,
        probability_of_precipitation_pct=precipitation,
    )


def _tomorrow_period(
    name: str,
    temp: int,
    short: str,
    precipitation: int | None,
) -> ForecastPeriod:
    now = datetime.now().astimezone()
    start = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return ForecastPeriod(
        name=name,
        start_time=start,
        end_time=start + timedelta(hours=12),
        is_daytime=True,
        temperature_f=temp,
        temperature_trend=None,
        wind_speed="5 mph",
        wind_direction="NW",
        short_forecast=short,
        detailed_forecast=short,
        probability_of_precipitation_pct=precipitation,
    )


def test_solar_fixed_date_is_local_and_provider_free() -> None:
    speech, payload = build_solar_response(
        "when is sunrise tomorrow",
        settings=SOLAR,
        now=datetime.fromisoformat("2026-03-07T12:00:00-05:00"),
    )

    assert speech == "Sunrise tomorrow at Dushore is at 7:29 AM."
    assert payload["date"] == "2026-03-08"
    assert str(payload["event_time"]).endswith("-04:00")
    assert payload["source_type"] == "astral_local_calculation"


def test_solar_civil_dusk_uses_six_degree_depression() -> None:
    _speech, payload = build_solar_response(
        "civil dusk on 2026-06-21", settings=SOLAR,
        now=datetime.fromisoformat("2026-06-01T12:00:00-04:00"),
    )

    assert payload["event"] == "civil_dusk"
    assert payload["evidence"]["civil_depression_degrees"] == 6


def test_solar_rejects_unconfigured_arbitrary_location() -> None:
    with pytest.raises(SolarLocationError, match="configured locations"):
        build_solar_response("sunset in Paris", settings=SOLAR)


def test_solar_reports_polar_no_event() -> None:
    polar = SolarWeatherLocationSettings(
        id="polar", label="Longyearbyen", aliases=(), latitude=78.2232,
        longitude=15.6469, timezone="Arctic/Longyearbyen",
    )
    settings = SolarWeatherRuntimeSettings(False, None, MappingProxyType({"polar": polar}))
    settings = SolarWeatherRuntimeSettings(True, None, settings.locations)

    with pytest.raises(SolarNoEventError, match="no sunrise"):
        build_solar_response(
            "sunrise in Longyearbyen on 2026-12-21", settings=settings,
            now=datetime.fromisoformat("2026-12-01T12:00:00+01:00"),
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("when does the sun come up tomorrow", True),
        ("sunset today", True),
        ("paint a sunset", False),
        ("set an alarm for sunset", False),
    ],
)
def test_solar_admission_requires_time_question_evidence(text: str, expected: bool) -> None:
    assert detect_solar_weather_query(text) is expected


def test_practical_umbrella_answer_leads_with_decision_and_evidence() -> None:
    speech = format_forecast_summary(
        "should I bring an umbrella tomorrow",
        [_tomorrow_period("Tomorrow", 61, "Rain Showers", 70)],
    )

    assert speech.startswith("Yes, bring an umbrella")
    assert "70 percent" in speech


def test_practical_coat_answer_leads_with_decision_and_temperature_range() -> None:
    speech = format_forecast_summary(
        "do I need a coat tomorrow", [_tomorrow_period("Tomorrow", 38, "Sunny", 5)]
    )

    assert speech.startswith("Yes, a coat is sensible")
    assert "38 to 38 degrees" in speech


def test_weather_handler_clarifies_household_schedule_anchor() -> None:
    clear_all_sessions()
    handler = WeatherHandler(Mock())
    dispatch = DispatchPlan(
        target="weather", hook="weather.weather_forecast",
        payload={
            "action": "weather_forecast", "text": "will it rain before I leave",
            "source": "kitchen", "session_id": "weather-anchor",
        }, status="pending_integration",
    )

    result = handler.handle(dispatch, object())

    assert result.status == "pending_clarification"
    assert result.result["speech"] == "What time should I use for that weather question?"
    assert get_pending_state("kitchen", "weather-anchor", domain="informational") is not None


def test_weather_handler_reuses_remote_location_for_window_followup() -> None:
    clear_all_sessions()
    execution = Mock()
    execution.build_remote_forecast_response.side_effect = [
        ("Boston forecast", {
            "requested_location": "boston", "location": "Boston, MA",
            "source_name": "National Weather Service", "source_type": "nws_forecast",
            "selected_periods": [],
        }),
        ("Boston Tuesday forecast", {
            "requested_location": "boston", "location": "Boston, MA",
            "source_name": "National Weather Service", "source_type": "nws_forecast",
            "selected_periods": [],
        }),
    ]
    handler = WeatherHandler(execution)
    first = DispatchPlan(
        target="weather", hook="weather.remote_weather_forecast",
        payload={"action": "remote_weather_forecast", "text": "weather tomorrow in boston", "source": "kitchen", "session_id": "weather-followup"},
        status="pending_integration",
    )
    second = DispatchPlan(
        target="weather", hook="weather.weather_forecast",
        payload={"action": "weather_forecast", "text": "what about tuesday", "source": "kitchen", "session_id": "weather-followup"},
        status="pending_integration",
    )

    assert handler.handle(first, object()).status == "executed"
    assert handler.handle(second, object()).status == "executed"
    assert execution.build_remote_forecast_response.call_args_list[1].args[0] == "what is the weather tuesday in boston"
    assert get_informational_context("kitchen", "weather-followup", domain="weather") is not None


def test_remote_bridge_caches_equivalent_location_and_current_reads() -> None:
    bridge = NominatimNwsRemoteWeatherBridge()
    location = ResolvedRemoteLocation("boston", "Boston, MA", 42.36, -71.06)
    bridge._resolve_uncached = Mock(return_value=location)  # type: ignore[method-assign]
    bridge._fetch_current_uncached = Mock(return_value={"temperature_f": 50})  # type: ignore[method-assign]

    first_location = bridge.resolve_location("Boston", user_agent="Oracle", timeout_seconds=2)
    second_location = bridge.resolve_location(" boston ", user_agent="Oracle", timeout_seconds=2)
    bridge.fetch_current(first_location, user_agent="Oracle", timeout_seconds=2)
    bridge.fetch_current(second_location, user_agent="Oracle", timeout_seconds=2)

    assert bridge._resolve_uncached.call_count == 1
    assert bridge._fetch_current_uncached.call_count == 1


def test_remote_bridge_normalizes_and_caches_active_alerts() -> None:
    bridge = NominatimNwsRemoteWeatherBridge()
    bridge._get_json = Mock(return_value={  # type: ignore[method-assign]
        "features": [{
            "id": "alert-1",
            "properties": {
                "event": "Severe Thunderstorm Warning", "headline": "Storm warning",
                "severity": "Severe", "urgency": "Immediate",
                "effective": "2026-09-06T16:00:00-04:00", "expires": "2026-09-06T17:00:00-04:00",
            },
        }]
    })

    first = bridge.fetch_alerts(
        latitude=41.59, longitude=-76.44, user_agent="Oracle", timeout_seconds=2
    )
    second = bridge.fetch_alerts(
        latitude=41.59, longitude=-76.44, user_agent="Oracle", timeout_seconds=2
    )

    assert first["alerts"][0]["severity"] == "Severe"
    assert second["freshness"] == "fresh"
    assert bridge._get_json.call_count == 1


def test_weather_ui_survives_current_failure_with_forecast_and_solar() -> None:
    execution = Mock()
    execution.build_current_response.side_effect = RuntimeError("station down")
    period = _period("Tonight", 55, "Clear", 5)
    execution.build_forecast_response.return_value = (
        "Tonight will be clear.", {"location": "Dushore", "state": "PA", "selected_periods": []}
    )
    execution.fetch_forecast.return_value = {"periods": [period]}
    execution.build_solar_snapshot.return_value = {
        "date": date(2026, 9, 6).isoformat(), "location": "Dushore",
        "events": {"sunrise": "2026-09-06T06:38:00-04:00", "sunset": "2026-09-06T19:31:00-04:00"},
    }
    execution.fetch_alerts.side_effect = RuntimeError("alerts down")

    payload = build_ui_weather_snapshot(canonical_execution=execution)

    assert payload["ok"] is True
    assert payload["components"] == {
        "current": "unavailable", "forecast": "available", "solar": "available", "alerts": "unavailable"
    }
    assert len(payload["forecast"]["periods"]) == 1
    assert payload["solar"]["location"] == "Dushore"


@pytest.mark.parametrize(
    ("available", "expected_components"),
    [
        ("current", {"current": "available", "forecast": "unavailable", "solar": "unavailable"}),
        ("forecast", {"current": "unavailable", "forecast": "available", "solar": "unavailable"}),
        ("solar", {"current": "unavailable", "forecast": "unavailable", "solar": "available"}),
    ],
)
def test_weather_ui_each_primary_component_can_support_partial_page(
    available: str, expected_components: dict[str, str]
) -> None:
    execution = Mock()
    execution.build_current_response.side_effect = RuntimeError("station down")
    execution.build_forecast_response.side_effect = RuntimeError("forecast down")
    execution.fetch_forecast.side_effect = RuntimeError("forecast down")
    execution.build_solar_snapshot.side_effect = RuntimeError("solar unavailable")
    execution.fetch_alerts.side_effect = RuntimeError("alerts down")
    if available == "current":
        execution.build_current_response.side_effect = None
        execution.build_current_response.return_value = (
            "It is 64 degrees.", {"temperature_f": 64, "location": "Dushore"}
        )
    elif available == "forecast":
        execution.build_forecast_response.side_effect = None
        execution.build_forecast_response.return_value = (
            "Tonight will be clear.", {"location": "Dushore", "state": "PA"}
        )
        execution.fetch_forecast.side_effect = None
        execution.fetch_forecast.return_value = {"periods": [_period("Tonight", 55, "Clear", 5)]}
    else:
        execution.build_solar_snapshot.side_effect = None
        execution.build_solar_snapshot.return_value = {
            "date": "2026-09-06", "location": "Dushore", "events": {"sunset": "2026-09-06T19:31:00-04:00"}
        }

    payload = build_ui_weather_snapshot(canonical_execution=execution)

    assert payload["ok"] is True
    for component, status in expected_components.items():
        assert payload["components"][component] == status


def test_weather_ui_fails_when_all_primary_components_are_unavailable() -> None:
    execution = Mock()
    execution.build_current_response.side_effect = RuntimeError("station down")
    execution.build_forecast_response.side_effect = RuntimeError("forecast down")
    execution.build_solar_snapshot.side_effect = RuntimeError("solar unavailable")
    execution.fetch_alerts.return_value = {"alerts": [], "freshness": "fresh"}

    with pytest.raises(RuntimeError, match="current weather, forecast, or solar"):
        build_ui_weather_snapshot(canonical_execution=execution)
