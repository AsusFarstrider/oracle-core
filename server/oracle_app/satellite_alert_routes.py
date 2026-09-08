from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request

from . import alerts as alerts_module
from .brain_application_composition import CanonicalBrainApplicationComposition
from .memory.alerts import acknowledge_alert, claim_due_alerts
from .alert_lifecycle import acknowledge_alert_occurrence
from .memory.alert_lifecycle import list_alert_occurrences, transition_alert_occurrence
from .notifications.channels.satellite_announcement import (
    ensure_active_satellite_receipts,
    reconcile_satellite_receipts,
    satellite_alert_claim_needs_work,
    transition_satellite_receipt,
)
from .schemas import (
    SatelliteAlertAcknowledgeRequest,
    SatelliteAlertAcknowledgeResponse,
    SatelliteAlertActionRequest,
    SatelliteAlertActionResponse,
    SatelliteAlertClaimRequest,
    SatelliteAlertClaimResponse,
    SatelliteAlertLease,
    SatelliteAlertStateResponse,
)
from .satellite_authentication import authenticate_satellite_source
from .session_state import resolve_request_session, set_utility_context
from .timers import build_timer_state, dismiss_timer
from .alarms import build_alarm_state, manage_alarm
from .reminders import build_reminder_state, manage_reminder


def satellite_alert_claim(
    payload: SatelliteAlertClaimRequest,
    request: Request,
) -> SatelliteAlertClaimResponse:
    composition, source_id = _authenticated_alert_source(request, payload.source_id)
    now = datetime.now(timezone.utc)
    if not satellite_alert_claim_needs_work(
        source_id,
        now=now,
        db_path=alerts_module.ALERT_DB_PATH,
    ):
        return SatelliteAlertClaimResponse(alerts=[])
    ensure_active_satellite_receipts(source_id)
    decisions = composition.notification_execution.build_delivery_decisions(source_id)
    alerts = claim_due_alerts(
        source_id=source_id,
        now=now,
        lease_seconds=payload.lease_seconds,
        limit=payload.limit,
        notification_decisions=decisions,
        exclude_kinds=("sleep_timer",),
        db_path=alerts_module.ALERT_DB_PATH,
    )
    reconcile_satellite_receipts(source_id)
    return SatelliteAlertClaimResponse(
        alerts=[
            SatelliteAlertLease(
                alert_id=alert.alert_id,
                lease_id=str(alert.lease_id),
                lease_expires_at=str(alert.lease_expires_at.isoformat()),
                kind=alert.kind,
                message=alert.message,
                due_at=alert.due_at.isoformat(),
                source_id=alert.source_id,
                session_id=alert.session_id,
                metadata=dict(alert.metadata),
            )
            for alert in alerts
        ]
    )


def satellite_alert_acknowledge(
    alert_id: str,
    payload: SatelliteAlertAcknowledgeRequest,
    request: Request,
) -> SatelliteAlertAcknowledgeResponse:
    _composition, source_id = _authenticated_alert_source(request, payload.source_id)
    try:
        alert = acknowledge_alert(
            alert_id=alert_id,
            source_id=source_id,
            lease_id=payload.lease_id,
            now=datetime.now(timezone.utc),
            completed=payload.status == "completed",
            db_path=alerts_module.ALERT_DB_PATH,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alert not found.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Alert source mismatch.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if alert.kind == "notification":
        transition_satellite_receipt(
            notification_type=str(alert.metadata.get("notification_id") or ""),
            occurrence_id=str(alert.metadata.get("event_id") or ""),
            source_id=source_id,
            status="accepted",
        )
    elif alert.kind in {"timer", "alarm", "reminder"} and alert.occurrence_id:
        occurrence = next(
            (
                item for item in list_alert_occurrences(db_path=alerts_module.ALERT_DB_PATH)
                if item.occurrence_id == alert.occurrence_id
            ),
            None,
        )
        if alert.kind == "reminder" and occurrence is not None and occurrence.status == "outstanding":
            acknowledge_alert_occurrence(
                occurrence_id=occurrence.occurrence_id,
                alert_id=alert.alert_id,
                actor_type="runtime",
                actor_id=source_id,
                action="delivery_accepted",
                idempotency_key=f"runtime:{alert.alert_id}:{payload.lease_id}",
                now=datetime.now(timezone.utc),
                db_path=alerts_module.ALERT_DB_PATH,
            )
        elif occurrence is not None and occurrence.status in {"due", "ringing"}:
            try:
                occurrence = transition_alert_occurrence(
                    alert.occurrence_id,
                    status="ringing",
                    actor_type="runtime",
                    actor_id=source_id,
                    reason=f"{alert.kind}_delivery_accepted",
                    now=datetime.now(timezone.utc),
                    db_path=alerts_module.ALERT_DB_PATH,
                )
            except ValueError:
                latest = next(
                    (
                        item for item in list_alert_occurrences(db_path=alerts_module.ALERT_DB_PATH)
                        if item.occurrence_id == alert.occurrence_id
                    ),
                    None,
                )
                if latest is None or latest.status not in {"completed", "canceled", "missed"}:
                    raise
                occurrence = latest
            if (
                occurrence is not None
                and occurrence.status == "ringing"
                and alert.metadata.get("completion_policy") == "delivery_accepted"
            ):
                owner_type = str(alert.metadata.get("completion_owner_type") or "").strip()
                owner_id = str(alert.metadata.get("completion_owner_id") or "").strip()
                if owner_type != "routine" or not owner_id:
                    raise ValueError("Delivery-consumed alert has invalid completion ownership metadata")
                acknowledge_alert_occurrence(
                    occurrence_id=occurrence.occurrence_id,
                    alert_id=alert.alert_id,
                    actor_type="system",
                    actor_id=f"routine:{owner_id}",
                    action="completed",
                    idempotency_key=f"routine-consumed:{occurrence.occurrence_id}",
                    now=datetime.now(timezone.utc),
                    db_path=alerts_module.ALERT_DB_PATH,
                )
    if alert.kind == "reminder" and payload.status == "completed" and str(alert.message or "").strip():
        session = resolve_request_session(source_id, payload.session_id)
        set_utility_context(
            source_id,
            str(session["effective_session_id"]),
            kind="repeat_output",
            payload={
                "reply_text": str(alert.message).strip(),
                "route_target": "system",
                "action": "reminder_delivery",
                "status": "executed",
                "error": "",
            },
        )
    return SatelliteAlertAcknowledgeResponse(
        alert_id=alert.alert_id,
        status="completed" if payload.status == "completed" else "acknowledged",
    )


def satellite_alert_state(source_id: str, request: Request) -> SatelliteAlertStateResponse:
    composition, authenticated_source = _authenticated_alert_source(request, source_id)
    timer_state = build_timer_state(
        source_id=authenticated_source,
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    alarm_state = build_alarm_state(
        source_id=authenticated_source,
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    reminder_state = build_reminder_state(
        source_id=authenticated_source,
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    return SatelliteAlertStateResponse.model_validate(
        {
            **timer_state,
            "alarms": alarm_state["alarms"],
            "reminders": reminder_state["reminders"],
            "outstanding": reminder_state["outstanding"],
            "ringing": [*timer_state["ringing"], *alarm_state["ringing"]],
            "count": int(timer_state["count"]) + int(alarm_state["count"]) + int(reminder_state["count"]),
            "display_attention_required": bool(alarm_state["ringing"] or reminder_state["outstanding"]),
        }
    )


def satellite_alert_action(
    occurrence_id: str,
    payload: SatelliteAlertActionRequest,
    request: Request,
) -> SatelliteAlertActionResponse:
    composition, source_id = _authenticated_alert_source(request, payload.source_id)
    try:
        try:
            result = manage_reminder(
                source_id=source_id, occurrence_id=occurrence_id, action=payload.action,
                snooze_minutes=payload.snooze_minutes,
                household=composition.runtime.household, satellites=composition.runtime.satellites,
                idempotency_key=payload.idempotency_key, db_path=alerts_module.ALERT_DB_PATH,
            )
        except KeyError:
            try:
                result = manage_alarm(
                    source_id=source_id, occurrence_id=occurrence_id, action=payload.action,
                    snooze_minutes=payload.snooze_minutes,
                    household=composition.runtime.household, satellites=composition.runtime.satellites,
                    idempotency_key=payload.idempotency_key, db_path=alerts_module.ALERT_DB_PATH,
                )
            except KeyError:
                if payload.action != "dismiss":
                    raise
                result = dismiss_timer(
                    occurrence_id,
                    source_id=source_id,
                    household=composition.runtime.household,
                    satellites=composition.runtime.satellites,
                    idempotency_key=payload.idempotency_key,
                    db_path=alerts_module.ALERT_DB_PATH,
                )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Active alert occurrence not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SatelliteAlertActionResponse.model_validate(result)


def _authenticated_alert_source(
    request: Request,
    claimed_source_id: str,
) -> tuple[CanonicalBrainApplicationComposition, str]:
    composition, source_id = authenticate_satellite_source(
        request,
        claimed_source_id=claimed_source_id,
    )
    satellite = composition.runtime.satellites.satellite_for_source(
        source_id
    )
    if satellite is None or not satellite.alert_capable:
        raise HTTPException(status_code=403, detail="Source is not an alert-capable satellite.")
    return composition, source_id


def register_satellite_alert_routes(app: FastAPI) -> None:
    app.get(
        "/api/satellite/alerts/state",
        response_model=SatelliteAlertStateResponse,
    )(satellite_alert_state)
    app.post(
        "/api/satellite/alerts/claim",
        response_model=SatelliteAlertClaimResponse,
    )(satellite_alert_claim)
    app.post(
        "/api/satellite/alerts/{alert_id}/acknowledge",
        response_model=SatelliteAlertAcknowledgeResponse,
    )(satellite_alert_acknowledge)
    app.post(
        "/api/satellite/alerts/{occurrence_id}/action",
        response_model=SatelliteAlertActionResponse,
    )(satellite_alert_action)
