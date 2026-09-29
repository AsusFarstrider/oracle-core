from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from oracle_app import application_ui


def test_compact_alert_state_includes_calendar_in_existing_alert_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui,
        "build_timer_state",
        lambda **kwargs: {
            "source_id": kwargs["source_id"], "generated_at": "2026-08-29T20:00:00+00:00",
            "count": 1, "timers": [{"occurrence_id": "timer-1"}], "ringing": [],
        },
    )
    monkeypatch.setattr(
        application_ui,
        "build_alarm_state",
        lambda **kwargs: {
            "source_id": kwargs["source_id"], "generated_at": "2026-08-29T20:00:01+00:00",
            "count": 1, "alarms": [{"schedule_id": "alarm-1"}], "ringing": [{"occurrence_id": "alarm-occurrence"}],
        },
    )
    monkeypatch.setattr(
        application_ui,
        "build_reminder_state",
        lambda **kwargs: {
            "source_id": kwargs["source_id"], "generated_at": "2026-08-29T20:00:02+00:00",
            "count": 1, "reminders": [{"schedule_id": "reminder-1"}],
            "outstanding": [{"occurrence_id": "reminder-occurrence"}],
        },
    )
    monkeypatch.setattr(
        application_ui,
        "build_calendar_alert_state",
        lambda **kwargs: {
            "count": 1,
            "calendar_alerts": [{
                "kind": "calendar", "occurrence_id": "calendar-occurrence",
                "status": "outstanding", "message": "Dentist appointment",
            }],
        },
    )
    composition = SimpleNamespace(
        runtime=SimpleNamespace(household=object(), satellites=object())
    )

    payload = application_ui._build_compact_alert_state("living-room", composition)

    assert payload["status"] == "ready"
    assert payload["source_id"] == "living-room"
    assert payload["active_count"] == 4
    assert payload["refresh_after_seconds"] == 2
    assert payload["generated_at"].endswith("+00:00")
    assert payload["timers"][0]["occurrence_id"] == "timer-1"
    assert payload["alarms"][0]["schedule_id"] == "alarm-1"
    assert payload["reminders"][0]["schedule_id"] == "reminder-1"
    assert [item["occurrence_id"] for item in payload["outstanding"]] == [
        "reminder-occurrence", "calendar-occurrence",
    ]
    assert payload["reminder_state"]["calendar_alerts"][0]["common_copy"] is True


def test_compact_alert_state_uses_idle_refresh_when_nothing_is_active(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui, "build_timer_state",
        lambda **kwargs: {"generated_at": "2026-08-29T20:00:00+00:00", "count": 0, "timers": [], "ringing": []},
    )
    monkeypatch.setattr(
        application_ui, "build_alarm_state",
        lambda **kwargs: {"generated_at": "2026-08-29T20:00:00+00:00", "count": 0, "alarms": [], "ringing": []},
    )
    monkeypatch.setattr(
        application_ui, "build_reminder_state",
        lambda **kwargs: {"generated_at": "2026-08-29T20:00:00+00:00", "count": 0, "reminders": [], "outstanding": []},
    )
    monkeypatch.setattr(
        application_ui, "build_calendar_alert_state",
        lambda **kwargs: {"count": 0, "calendar_alerts": []},
    )
    composition = SimpleNamespace(runtime=SimpleNamespace(household=object(), satellites=object()))

    assert application_ui._build_compact_alert_state("living-room", composition)["refresh_after_seconds"] == 30


def test_compact_alert_state_is_source_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui, "_require_stable_alert_ui_request",
        lambda source_id, request: SimpleNamespace(request_source_id="office"),
    )

    with pytest.raises(HTTPException) as exc:
        application_ui._ui_alert_state_impl("living-room", object())

    assert exc.value.status_code == 403


def test_calendar_card_dismiss_uses_canonical_calendar_lifecycle(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui,
        "_require_stable_alert_ui_request",
        lambda source_id, request: SimpleNamespace(request_source_id=source_id),
    )
    monkeypatch.setattr(
        application_ui,
        "brain_application_composition",
        lambda app: SimpleNamespace(runtime=SimpleNamespace(household=object(), satellites=object())),
    )
    monkeypatch.setattr(
        application_ui,
        "build_calendar_alert_state",
        lambda **kwargs: {
            "count": 1,
            "calendar_alerts": [{"occurrence_id": "calendar-occurrence"}],
        },
    )
    calls = []
    monkeypatch.setattr(
        application_ui,
        "dismiss_calendar_alert",
        lambda occurrence_id, **kwargs: calls.append((occurrence_id, kwargs)) or {
            "ok": True, "action": "dismiss", "occurrence_id": occurrence_id,
        },
    )
    monkeypatch.setattr(
        application_ui,
        "manage_reminder",
        lambda **kwargs: pytest.fail("Calendar dismissal must not use ordinary reminder mutation"),
    )

    result = application_ui._ui_reminder_action_impl(
        SimpleNamespace(
            client_id="kiosk", source_id="office_satellite",
            occurrence_id="calendar-occurrence", action="dismiss", snooze_minutes=10,
        ),
        SimpleNamespace(app=object()),
    )

    assert result["action"] == "dismiss"
    assert calls[0][0] == "calendar-occurrence"
    assert calls[0][1]["source_id"] == "office_satellite"
