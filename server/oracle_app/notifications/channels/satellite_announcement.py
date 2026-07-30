from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from oracle_app.alerts import create_alert_batch, list_due_alerts
from oracle_app.config import get_notification_settings

from ..audit import record_notification_event
from ..policy import SuppressionStatus, evaluate_notification_suppression


def dispatch_satellite_announcement(
    *,
    notification_type: str,
    occurrence_id: str,
    definition: dict[str, Any],
    caller: str,
    now: datetime,
) -> dict[str, Any]:
    return dispatch_satellite_announcement_values(
        notification_type=notification_type,
        occurrence_id=occurrence_id,
        targets=tuple(str(value) for value in definition.get("targets") or []),
        message=str(definition.get("message") or ""),
        audio_policy=str(definition.get("audio_policy") or ""),
        delivery_ttl_seconds=int(definition.get("delivery_ttl_seconds") or 0),
        caller=caller,
        now=now,
    )


def dispatch_satellite_announcement_values(
    *,
    notification_type: str,
    occurrence_id: str,
    targets: tuple[str, ...],
    message: str,
    audio_policy: str,
    delivery_ttl_seconds: int,
    caller: str,
    now: datetime,
) -> dict[str, Any]:
    metadata = {
        "notification_id": notification_type,
        "event_id": occurrence_id,
        "audio_policy": audio_policy,
    }
    if caller != "home_assistant":
        metadata["caller"] = caller
    alerts, duplicate = create_alert_batch(
        kind="notification",
        due_at=now,
        message=message,
        sources=targets,
        session_id=None,
        metadata=metadata,
        expires_at=now + timedelta(seconds=delivery_ttl_seconds),
        idempotency_key=f"notification:{notification_type}:{occurrence_id}",
    )
    return {
        "status": "duplicate" if duplicate else "queued",
        "queued_targets": [] if duplicate else [str(alert.source) for alert in alerts],
        "target_count": 0 if duplicate else len(alerts),
    }


def build_satellite_delivery_decisions(
    source: str | None,
    *,
    now: datetime,
    settings: dict[str, Any] | None = None,
    suppression_evaluator: Callable[..., SuppressionStatus] | None = None,
) -> dict[str, str]:
    due = list_due_alerts(source, kind="notification")
    if not due:
        return {}

    resolved_settings = settings or get_notification_settings()
    resolved_suppression_evaluator = suppression_evaluator or evaluate_notification_suppression
    definitions = resolved_settings.get("notifications") or {}
    status_by_notification: dict[str, SuppressionStatus] = {}
    decisions: dict[str, str] = {}
    for alert in due:
        notification_type = str(alert.metadata.get("notification_id") or "").strip().lower()
        occurrence_id = str(alert.metadata.get("event_id") or "").strip()
        caller = str(alert.metadata.get("caller") or "home_assistant").strip() or "home_assistant"
        if alert.expires_at is not None and alert.expires_at <= now:
            decisions[alert.alert_id] = "suppress"
            record_notification_event(
                notification_type=notification_type,
                occurrence_id=occurrence_id,
                status="expired",
                caller=caller,
                target_source=str(alert.source or ""),
            )
            continue
        definition = definitions.get(notification_type)
        if not isinstance(definition, dict) or definition.get("enabled") is not True:
            decisions[alert.alert_id] = "suppress"
            continue
        if notification_type not in status_by_notification:
            status_by_notification[notification_type] = resolved_suppression_evaluator(
                definition,
                settings=resolved_settings,
            )
        suppression = status_by_notification[notification_type]
        if suppression == "active":
            decisions[alert.alert_id] = "suppress"
            record_notification_event(
                notification_type=notification_type,
                occurrence_id=occurrence_id,
                status="suppressed_before_delivery",
                caller=caller,
                target_source=str(alert.source or ""),
            )
        elif suppression == "inactive":
            decisions[alert.alert_id] = "deliver"
        else:
            decisions[alert.alert_id] = "defer"
    return decisions
