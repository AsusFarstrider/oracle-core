from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request

from oracle_app.memory.alert_lifecycle import (
    list_alert_occurrences,
    transition_alert_occurrence,
)
from oracle_app.memory.alerts import claim_due_alerts, create_alert_records, list_alert_records
from oracle_app.memory.sources import seed_sources
from oracle_app.orchestration_routines import (
    configure_routine_adapters,
    resume_due_routines,
    start_routine,
)
from oracle_app.schemas import SatelliteAlertAcknowledgeRequest
from oracle_app.satellite_alert_routes import satellite_alert_acknowledge


SOURCE = "test_satellite_alpha"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _accept_delivery(*, db_path: Path, alert_id: str, lease_id: str) -> None:
    with patch(
        "oracle_app.satellite_alert_routes._authenticated_alert_source",
        return_value=(SimpleNamespace(), SOURCE),
    ), patch("oracle_app.satellite_alert_routes.alerts_module.ALERT_DB_PATH", db_path):
        satellite_alert_acknowledge(
            alert_id,
            SatelliteAlertAcknowledgeRequest(
                source_id=SOURCE,
                lease_id=lease_id,
                status="acknowledged",
            ),
            _request(),
        )


def test_routine_consumes_presented_timer_before_advancing_to_media(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    seed_sources(
        [{"source_id": SOURCE, "source_type": "satellite", "display_name": "Kitchen Display"}],
        db_path=db_path,
    )
    clock = datetime.now(UTC)
    media_starts: list[dict[str, object]] = []

    def timer_sound(*, source_id: str, occurrence_id: str, **_kwargs: object) -> dict[str, object]:
        alerts, duplicate = create_alert_records(
            kind="timer",
            due_at=clock,
            message="Timer finished.",
            source_ids=[source_id],
            session_id=occurrence_id,
            metadata={
                "caller": "orchestration",
                "operation": "timer_sound",
                "completion_policy": "delivery_accepted",
                "completion_owner_type": "routine",
                "completion_owner_id": occurrence_id,
            },
            idempotency_key=f"routine-timer-sound:{occurrence_id}",
            db_path=db_path,
        )
        return {
            "ok": True,
            "status": "duplicate" if duplicate else "queued",
            "occurrence_id": None if duplicate else alerts[0].occurrence_id,
        }

    configure_routine_adapters(
        ui_action=lambda **_kwargs: {"ok": True},
        audiobook_start=lambda **kwargs: media_starts.append(kwargs) or {"ok": True},
        sleep_timer=lambda **_kwargs: {"ok": True},
        state_check=lambda **_kwargs: {"ok": True},
        playback_check=lambda **_kwargs: {"ok": True},
        timer_sound=timer_sound,
    )
    definition = {
        "id": "test_bedtime",
        "display_name": "Test Bedtime",
        "enabled": True,
        "user_id": "test",
        "source_ids": [SOURCE],
        "inputs": {},
        "steps": [
            {
                "id": "bedtime_delay",
                "type": "wait",
                "label": "Bedtime delay",
                "duration_seconds": 1,
                "max_lateness_seconds": 30,
                "required": True,
            },
            {
                "id": "timer_sound",
                "type": "timer_sound",
                "label": "Timer sound",
                "source_id": SOURCE,
                "required": True,
            },
            {
                "id": "final_wait",
                "type": "wait",
                "label": "Final wait",
                "duration_seconds": 1,
                "max_lateness_seconds": 30,
                "required": True,
            },
            {
                "id": "book",
                "type": "audiobook_start",
                "label": "Start book",
                "source_id": SOURCE,
                "user_id": "test",
                "required": True,
            },
        ],
    }

    with patch("oracle_app.orchestration_routines._utc_datetime", return_value=clock):
        started = start_routine(
            "test_bedtime",
            client_id="test-bedtime-client",
            definition=definition,
            db_path=db_path,
        )
    first_due = datetime.fromisoformat(started["steps"][0]["payload"]["due_at"])
    with patch("oracle_app.orchestration_routines._utc_datetime", return_value=first_due):
        waiting = resume_due_routines(now=first_due, db_path=db_path)[0]

    occurrence = list_alert_occurrences(db_path=db_path)[0]
    transition_alert_occurrence(
        occurrence.occurrence_id,
        status="due",
        actor_type="system",
        actor_id=None,
        reason="became_due",
        now=clock,
        db_path=db_path,
    )
    leased = claim_due_alerts(source_id=SOURCE, now=clock, db_path=db_path)[0]
    _accept_delivery(db_path=db_path, alert_id=leased.alert_id, lease_id=str(leased.lease_id))

    assert list_alert_occurrences(db_path=db_path)[0].status == "completed"
    assert list_alert_records(source_id=SOURCE, kind="timer", db_path=db_path)[0].status == "acknowledged"
    assert not [
        item
        for item in list_alert_records(source_id=SOURCE, kind="timer", db_path=db_path)
        if item.status in {"pending", "leased"}
    ]
    assert claim_due_alerts(source_id=SOURCE, now=clock + timedelta(seconds=2), db_path=db_path) == []
    assert media_starts == []

    second_due = datetime.fromisoformat(waiting["steps"][2]["payload"]["due_at"])
    completed = resume_due_routines(now=second_due, db_path=db_path)[0]
    assert completed["status"] == "completed"
    assert len(media_starts) == 1
    assert media_starts[0]["user_id"] == "test"
    assert claim_due_alerts(source_id=SOURCE, now=second_due, db_path=db_path) == []


def test_standalone_timer_still_requires_human_or_destination_acknowledgement(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    seed_sources(
        [{"source_id": SOURCE, "source_type": "satellite", "display_name": "Kitchen Display"}],
        db_path=db_path,
    )
    clock = datetime.now(UTC)
    alerts, _duplicate = create_alert_records(
        kind="timer",
        due_at=clock,
        message="Timer finished.",
        source_ids=[SOURCE],
        session_id="ordinary-user-timer",
        metadata={},
        db_path=db_path,
    )
    occurrence_id = str(alerts[0].occurrence_id)
    transition_alert_occurrence(
        occurrence_id,
        status="due",
        actor_type="system",
        actor_id=None,
        reason="became_due",
        now=clock,
        db_path=db_path,
    )
    leased = claim_due_alerts(source_id=SOURCE, now=clock, db_path=db_path)[0]

    _accept_delivery(db_path=db_path, alert_id=leased.alert_id, lease_id=str(leased.lease_id))

    assert list_alert_occurrences(db_path=db_path)[0].status == "ringing"
    assert list_alert_records(source_id=SOURCE, kind="timer", db_path=db_path)[0].status == "acknowledged"
