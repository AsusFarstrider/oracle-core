from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import oracle_app.alerts as alerts_module
from oracle_app.alerts import create_alert_batch, list_due_alerts
from oracle_app.memory.alerts import list_alert_records
from oracle_app.memory.schema import ensure_schema
from oracle_app.memory.store import DB_PATH, transaction
from oracle_app.notifications.receipts import (
    NotificationDeliveryQuery,
    list_notification_deliveries,
    reserve_notification_delivery,
    transition_notification_delivery,
)

from ..audit import record_notification_event
from ..policy import SuppressionStatus, evaluate_notification_suppression


def satellite_alert_claim_needs_work(
    source_id: str,
    *,
    now: datetime,
    db_path: Path | None = None,
) -> bool:
    """Return whether a satellite claim needs the durable delivery path.

    Authentication remains at the route. This preflight only avoids repeated
    reconciliation and claim scans when there is no eligible alert work and no
    recoverable satellite receipt. Active notification alerts are always work
    because they may need crash repair before they become due.
    """

    clean_source = str(source_id or "").strip()
    if not clean_source:
        raise ValueError("source_id is required")
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    clock = now.astimezone(timezone.utc).isoformat()
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        source = conn.execute(
            "SELECT status FROM memory_sources WHERE source_id=?", (clean_source,)
        ).fetchone()
        if source is None or str(source["status"]) != "active":
            raise ValueError(
                f"Alert source {clean_source!r} is not an active canonical identity."
            )
        alert_work = conn.execute(
            """SELECT 1 FROM memory_alerts
               WHERE source_id=? AND kind!='sleep_timer' AND (
                   (kind='notification' AND status IN ('pending', 'leased'))
                   OR (status='pending' AND due_at<=?)
                   OR (status='leased' AND lease_expires_at<=?)
               )
               LIMIT 1""",
            (clean_source, clock, clock),
        ).fetchone()
        if alert_work is not None:
            return True
        receipt_work = conn.execute(
            """SELECT 1 FROM memory_notification_deliveries
               WHERE channel='satellite_announcement' AND destination_id=?
                 AND status IN ('pending', 'retry_wait')
               LIMIT 1""",
            (clean_source,),
        ).fetchone()
    return receipt_work is not None


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
    receipt_statuses: list[str] = []
    expires_at = (now + timedelta(seconds=delivery_ttl_seconds)).isoformat()
    for target in targets:
        receipt, created = reserve_notification_delivery(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            channel="satellite_announcement",
            destination_id=target,
            provider="oracle_brain",
            max_attempts=1,
            retry_seconds=30,
            expires_at=expires_at,
            failure_policy="best_effort",
            repeat_policy="every_occurrence",
            db_path=alerts_module.ALERT_DB_PATH,
        )
        receipt_statuses.append("queued" if created else str(receipt["status"]))
    return {
        "status": "duplicate" if duplicate else "queued",
        "queued_targets": [] if duplicate else [str(alert.source) for alert in alerts],
        "target_count": 0 if duplicate else len(alerts),
        "receipt_statuses": receipt_statuses,
    }


def reserve_suppressed_satellite_receipts(
    *,
    notification_type: str,
    occurrence_id: str,
    targets: tuple[str, ...],
    expires_at: datetime,
) -> bool:
    created_any = False
    for target in targets:
        receipt, created = reserve_notification_delivery(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            channel="satellite_announcement",
            destination_id=target,
            provider="oracle_brain",
            max_attempts=1,
            retry_seconds=30,
            expires_at=expires_at.isoformat(),
            failure_policy="best_effort",
            repeat_policy="every_occurrence",
            db_path=alerts_module.ALERT_DB_PATH,
        )
        if created:
            transition_notification_delivery(
                str(receipt["receipt_id"]),
                status="suppressed",
                db_path=alerts_module.ALERT_DB_PATH,
            )
            created_any = True
    return created_any


def transition_satellite_receipt(
    *,
    notification_type: str,
    occurrence_id: str,
    source_id: str,
    status: str,
) -> None:
    matches = list_notification_deliveries(
        NotificationDeliveryQuery(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            channel="satellite_announcement",
            destination_id=source_id,
            limit=2,
        ),
        db_path=alerts_module.ALERT_DB_PATH,
    )
    if len(matches) != 1:
        raise RuntimeError("Satellite notification receipt is missing or ambiguous.")
    receipt = matches[0]
    if str(receipt["status"]) in {"pending", "retry_wait"}:
        transition_notification_delivery(
            str(receipt["receipt_id"]),
            status=status,
            db_path=alerts_module.ALERT_DB_PATH,
        )


def reconcile_satellite_receipts(source_id: str) -> None:
    """Converge notification receipts after alert terminal transitions.

    This is retry-safe and deliberately runs on every claim so a crash between
    the two required Memory mutations cannot strand a receipt as pending.
    """
    terminal = list_alert_records(
        source_id=source_id,
        kind="notification",
        statuses=("acknowledged", "completed", "canceled", "expired"),
        db_path=alerts_module.ALERT_DB_PATH,
    )
    for alert in terminal:
        notification_type = str(alert.metadata.get("notification_id") or "").strip()
        occurrence_id = str(alert.metadata.get("event_id") or "").strip()
        if not notification_type or not occurrence_id:
            raise RuntimeError("Terminal notification alert lacks receipt identity.")
        status = {
            "acknowledged": "accepted",
            "completed": "accepted",
            "canceled": "suppressed",
            "expired": "expired",
        }[alert.status]
        transition_satellite_receipt(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            source_id=source_id,
            status=status,
        )


def ensure_active_satellite_receipts(source_id: str) -> None:
    """Repair a crash between notification-alert and receipt reservation."""
    active = list_alert_records(
        source_id=source_id,
        kind="notification",
        statuses=("pending", "leased"),
        db_path=alerts_module.ALERT_DB_PATH,
    )
    for alert in active:
        notification_type = str(alert.metadata.get("notification_id") or "").strip()
        occurrence_id = str(alert.metadata.get("event_id") or "").strip()
        if not notification_type or not occurrence_id or alert.expires_at is None:
            raise RuntimeError("Active notification alert lacks receipt identity or expiry.")
        reserve_notification_delivery(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            channel="satellite_announcement",
            destination_id=source_id,
            provider="oracle_brain",
            max_attempts=1,
            retry_seconds=30,
            expires_at=alert.expires_at.isoformat(),
            failure_policy="best_effort",
            repeat_policy="every_occurrence",
            db_path=alerts_module.ALERT_DB_PATH,
        )


def build_satellite_delivery_decisions(
    source: str | None,
    *,
    now: datetime,
    settings: dict[str, Any],
    suppression_evaluator: Callable[..., SuppressionStatus],
) -> dict[str, str]:
    due = list_due_alerts(source, kind="notification")
    if not due:
        return {}

    definitions = settings.get("notifications") or {}
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
            status_by_notification[notification_type] = suppression_evaluator(
                definition,
                settings=settings,
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
