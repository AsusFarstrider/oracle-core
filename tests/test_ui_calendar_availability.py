from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from oracle_app import application_ui
from oracle_app.calendar_runtime import (
    CalendarReadUnavailableError,
    CanonicalCalendarExecution,
)
from oracle_app.provider_bridges.nextcloud_calendar import (
    CalendarBridgeConfigurationError,
    CalendarBridgeError,
    NextcloudCalendarBridge,
)
from oracle_app.ui_satellite import build_satellite_ui_home_snapshot
from oracle_app.ui_snapshot_cache import clear_cached_snapshots


def _calendar_execution() -> CanonicalCalendarExecution:
    feed = SimpleNamespace(id="personal", resolved_url="https://calendar.invalid/events.ics")
    read = SimpleNamespace(
        enabled=True,
        fresh_seconds=0,
        stale_if_error_seconds=0,
        feeds_for_kind=lambda _kind: (feed,),
    )
    settings = SimpleNamespace(
        read=read,
        write=SimpleNamespace(enabled=False),
        config_revision="calendar-test",
        timezone="America/New_York",
        timeout_seconds=1,
    )
    return CanonicalCalendarExecution(settings)  # type: ignore[arg-type]


def _satellite_settings():
    ui = SimpleNamespace(
        enabled=True,
        pages=["home", "calendar"],
        bottom_nav=["home", "calendar"],
        touch=True,
        profile="household",
        layout="satellite_landscape_touch_v1",
    )
    satellite = SimpleNamespace(
        satellite_id="example_display_satellite",
        source_id="example_room_satellite",
        ui=ui,
        platform="windows",
        capabilities=SimpleNamespace(
            voice=True,
            display=True,
            music_playback=True,
            audiobook_playback=True,
        ),
    )
    fleet = SimpleNamespace(
        entries={satellite.satellite_id: satellite},
        entry=lambda value: satellite if value in {satellite.satellite_id, satellite.source_id} else None,
    )
    room = SimpleNamespace(id="example_room", display_name="Example Room")
    household = SimpleNamespace(
        source=lambda value: SimpleNamespace(id=value, associated_room_id="example_room"),
        configured_associated_room_id=lambda _value: "example_room",
        room=lambda value: room if value == "example_room" else None,
    )
    return fleet, household


def test_canonical_provider_failure_is_typed_as_read_unavailable() -> None:
    execution = _calendar_execution()
    execution.bridge = SimpleNamespace(
        fetch_typed_events=lambda **_kwargs: (_ for _ in ()).throw(
            CalendarBridgeError("calendar_query_failed", "connection refused")
        )
    )

    with pytest.raises(CalendarReadUnavailableError) as caught:
        execution.load_events(scope="personal")

    assert caught.value.error_code == "calendar_query_failed"
    assert caught.value.detail == "connection refused"


@patch("oracle_app.provider_bridges.nextcloud_calendar.request.urlopen", side_effect=TimeoutError("timed out"))
def test_calendar_bridge_types_response_timeout_as_provider_failure(_urlopen: Mock) -> None:
    with pytest.raises(CalendarBridgeError) as caught:
        NextcloudCalendarBridge().fetch_typed_events(
            feed_url="https://calendar.invalid/events.ics",
            timeout_seconds=1,
            timezone_name="America/New_York",
        )

    assert caught.value.error_code == "calendar_query_failed"
    assert caught.value.detail == "timed out"


def test_unexpected_calendar_failure_remains_strict() -> None:
    execution = _calendar_execution()
    execution.bridge = SimpleNamespace(
        fetch_typed_events=lambda **_kwargs: (_ for _ in ()).throw(ValueError("malformed calendar data"))
    )

    with pytest.raises(ValueError, match="malformed calendar data"):
        execution.load_events(scope="personal")


def test_calendar_configuration_failure_remains_strict() -> None:
    execution = _calendar_execution()
    execution.bridge = SimpleNamespace(
        fetch_typed_events=lambda **_kwargs: (_ for _ in ()).throw(
            CalendarBridgeConfigurationError(
                "calendar_unconfigured",
                "calendar feed is not configured",
            )
        )
    )

    with pytest.raises(CalendarBridgeConfigurationError):
        execution.load_events(scope="personal")


def test_calendar_summary_contains_provider_failure_and_caches_degraded_snapshot() -> None:
    clear_cached_snapshots()
    execution = SimpleNamespace(settings=SimpleNamespace(timezone="America/New_York"))
    composition = SimpleNamespace(calendar_execution=execution)
    unavailable = CalendarReadUnavailableError(
        "connection refused",
        error_code="calendar_query_failed",
    )

    with (
        patch.object(application_ui, "brain_application_composition", return_value=composition),
        patch.object(application_ui, "_build_ui_calendar_snapshot", side_effect=unavailable) as build,
    ):
        first = application_ui._cached_ui_calendar_snapshot(limit=4)
        second = application_ui._cached_ui_calendar_snapshot(limit=4)

    assert first == {
        "events": [],
        "status": "unavailable",
        "detail": "Calendar is temporarily unavailable.",
    }
    assert second == first
    build.assert_called_once()


def test_calendar_page_contains_provider_failure_with_renderable_contract() -> None:
    clear_cached_snapshots()
    execution = SimpleNamespace(settings=SimpleNamespace(timezone="America/New_York"))
    composition = SimpleNamespace(calendar_execution=execution)

    with (
        patch.object(application_ui, "brain_application_composition", return_value=composition),
        patch.object(
            application_ui,
            "_build_ui_calendar_page_snapshot",
            side_effect=CalendarReadUnavailableError(
                "connection refused",
                error_code="calendar_query_failed",
            ),
        ),
    ):
        payload = application_ui._cached_ui_calendar_page_snapshot()

    assert payload["status"] == "unavailable"
    assert payload["today"]["events"] == []
    assert payload["upcoming"]["events"] == []
    assert payload["create_event"] == {
        "available": False,
        "status": "unavailable",
        "detail": "Calendar is temporarily unavailable.",
    }


def test_household_home_keeps_unrelated_components_when_calendar_is_unavailable() -> None:
    clear_cached_snapshots()
    execution = SimpleNamespace(settings=SimpleNamespace(timezone="America/New_York"))
    composition = SimpleNamespace(
        calendar_execution=execution,
        network_execution=None,
        runtime=SimpleNamespace(
            home_assistant=None,
            household=SimpleNamespace(ui=SimpleNamespace(escape_hatches={})),
        ),
    )

    with (
        patch.object(application_ui, "brain_application_composition", return_value=composition),
        patch.object(application_ui, "_cached_ui_home_weather_payload", return_value={"summary": "Clear"}),
        patch.object(
            application_ui,
            "_cached_ui_network_health_snapshot",
            return_value={"status": "healthy"},
        ),
        patch.object(
            application_ui,
            "_build_ui_calendar_snapshot",
            side_effect=CalendarReadUnavailableError(
                "connection refused",
                error_code="calendar_query_failed",
            ),
        ),
    ):
        payload = application_ui._build_ui_home_snapshot()

    assert payload["weather"] == {"summary": "Clear"}
    assert payload["network_health"] == {"status": "healthy"}
    assert payload["calendar"]["status"] == "unavailable"


def test_ui_calendar_boundary_does_not_hide_unexpected_failure() -> None:
    clear_cached_snapshots()
    composition = SimpleNamespace(
        calendar_execution=SimpleNamespace(settings=SimpleNamespace(timezone="America/New_York"))
    )

    with (
        patch.object(application_ui, "brain_application_composition", return_value=composition),
        patch.object(
            application_ui,
            "_build_ui_calendar_snapshot",
            side_effect=RuntimeError("unexpected implementation defect"),
        ),
        pytest.raises(RuntimeError, match="unexpected implementation defect"),
    ):
        application_ui._cached_ui_calendar_snapshot(limit=4)


@patch("oracle_app.ui_satellite.list_alerts", return_value=[])
def test_satellite_home_renders_when_calendar_component_is_unavailable(_list_alerts: Mock) -> None:
    fleet, household = _satellite_settings()

    payload = build_satellite_ui_home_snapshot(
        "example_display_satellite",
        build_ui_home_snapshot=lambda: {"weather": {"summary": "Clear"}},
        build_ui_audio_status_snapshot=lambda _source: {
            "source": "example_room_satellite",
            "available_sources": [],
            "playback": {"ok": False, "active": False, "output_owner": None},
        },
        build_ui_calendar_snapshot=lambda **_kwargs: {
            "events": [],
            "status": "unavailable",
            "detail": "Calendar is temporarily unavailable.",
        },
        home_assistant_settings=None,
        fleet_settings=fleet,
        household_settings=household,
        routine_settings=None,
    )

    assert payload["satellite"]["satellite_id"] == "example_display_satellite"
    assert payload["weather"]["summary"] == "Clear"
    assert payload["calendar"]["status"] == "unavailable"
    assert payload["room_controls"]["state"] == "unavailable"
