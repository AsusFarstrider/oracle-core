from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from oracle_app import application_ui


def test_compact_alert_state_is_one_projection_of_all_three_owners(monkeypatch) -> None:
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
    composition = SimpleNamespace(
        runtime=SimpleNamespace(household=object(), satellites=object())
    )

    payload = application_ui._build_compact_alert_state("living-room", composition)

    assert payload["status"] == "ready"
    assert payload["source_id"] == "living-room"
    assert payload["active_count"] == 3
    assert payload["refresh_after_seconds"] == 2
    assert payload["generated_at"].endswith("+00:00")
    assert payload["timers"][0]["occurrence_id"] == "timer-1"
    assert payload["alarms"][0]["schedule_id"] == "alarm-1"
    assert payload["reminders"][0]["schedule_id"] == "reminder-1"


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
