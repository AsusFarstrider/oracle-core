from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request

from . import alerts as alerts_module
from .brain_application_composition import CanonicalBrainApplicationComposition
from .memory.alerts import acknowledge_alert, claim_due_alerts
from .notifications.channels.satellite_announcement import (
    ensure_active_satellite_receipts,
    reconcile_satellite_receipts,
    satellite_alert_claim_needs_work,
    transition_satellite_receipt,
)
from .schemas import (
    SatelliteAlertAcknowledgeRequest,
    SatelliteAlertAcknowledgeResponse,
    SatelliteAlertClaimRequest,
    SatelliteAlertClaimResponse,
    SatelliteAlertLease,
)
from .satellite_authentication import authenticate_satellite_source


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
    return SatelliteAlertAcknowledgeResponse(
        alert_id=alert.alert_id,
        status="completed" if payload.status == "completed" else "acknowledged",
    )


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
    app.post(
        "/api/satellite/alerts/claim",
        response_model=SatelliteAlertClaimResponse,
    )(satellite_alert_claim)
    app.post(
        "/api/satellite/alerts/{alert_id}/acknowledge",
        response_model=SatelliteAlertAcknowledgeResponse,
    )(satellite_alert_acknowledge)
