from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

from .audit import record_notification_event
from .catalog import resolve_notification_definition
from .channels.satellite_announcement import (
    build_satellite_delivery_decisions,
    dispatch_satellite_announcement,
    reserve_suppressed_satellite_receipts,
)
from .channels.external import reserve_external_deliveries
from .errors import (
    NotificationContextNotSupportedError,
    NotificationRequestError,
    NotificationSuppressionUnavailableError,
)
from .policy import evaluate_notification_suppression


def submit_notification(
    notification_type: str,
    occurrence_id: str,
    *,
    context: dict[str, Any] | None = None,
    caller: str = "oracle",
    correlation_id: str = "",
) -> dict[str, Any]:
    clean_occurrence_id = str(occurrence_id or "").strip()
    clean_caller = str(caller or "").strip().lower()
    if not clean_occurrence_id:
        raise NotificationRequestError("occurrence_id is required")
    if not clean_caller:
        raise NotificationRequestError("caller is required")
    if context:
        raise NotificationContextNotSupportedError(
            "Current notification definitions do not declare context fields."
        )

    clean_type, definition, settings = resolve_notification_definition(notification_type)
    suppression = evaluate_notification_suppression(definition, settings=settings)
    if suppression == "unavailable":
        record_notification_event(
            notification_type=clean_type,
            occurrence_id=clean_occurrence_id,
            status="suppression_unavailable",
            caller=clean_caller,
            correlation_id=correlation_id,
        )
        raise NotificationSuppressionUnavailableError(clean_type)
    if suppression == "active":
        created = reserve_suppressed_satellite_receipts(
            notification_type=clean_type,
            occurrence_id=clean_occurrence_id,
            targets=tuple(str(value) for value in definition.get("targets") or ()),
            expires_at=_now_local()
            + timedelta(seconds=int(definition.get("delivery_ttl_seconds") or 1)),
        )
        status = "suppressed" if created else "duplicate"
        record_notification_event(
            notification_type=clean_type,
            occurrence_id=clean_occurrence_id,
            status=status,
            caller=clean_caller,
            correlation_id=correlation_id,
        )
        return _result(clean_type, clean_occurrence_id, status=status)

    satellite_result = dispatch_satellite_announcement(
        notification_type=clean_type,
        occurrence_id=clean_occurrence_id,
        definition=definition,
        caller=clean_caller,
        now=_now_local(),
    )
    satellite_status = str(satellite_result["status"])
    channel_results: dict[str, dict[str, Any]] = {
        "satellite_announcement": {
            "channel": "satellite_announcement",
            "status": satellite_status,
            "queued_targets": list(satellite_result["queued_targets"]),
            "target_count": int(satellite_result["target_count"]),
        }
    }
    status = satellite_status
    external_policy = definition.get("external_delivery")
    if isinstance(external_policy, dict) and external_policy.get("enabled") is True:
        try:
            external_result = reserve_external_deliveries(
                notification_type=clean_type,
                occurrence_id=clean_occurrence_id,
                correlation_id=correlation_id,
                definition=definition,
                settings=settings,
            )
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            external_result = {
                "channel": "external",
                "status": "failed",
                "error_class": type(exc).__name__,
            }
            if str(external_policy.get("failure_policy") or "") == "required":
                record_notification_event(
                    notification_type=clean_type,
                    occurrence_id=clean_occurrence_id,
                    status="failed",
                    caller=clean_caller,
                    target_count=int(satellite_result["target_count"]),
                    correlation_id=correlation_id,
                )
                raise NotificationRequestError(
                    "Required external notification work could not be accepted."
                ) from exc
            status = "partial"
        else:
            if satellite_status == "duplicate" and external_result["status"] == "queued":
                status = "queued"
        channel_results["external"] = external_result
    record_notification_event(
        notification_type=clean_type,
        occurrence_id=clean_occurrence_id,
        status=status,
        caller=clean_caller,
        target_count=int(satellite_result["target_count"]),
        correlation_id=correlation_id,
    )
    return _result(
        clean_type,
        clean_occurrence_id,
        status=status,
        queued_targets=list(satellite_result["queued_targets"]),
        channel_results=channel_results,
    )


def emit_notification(
    notification_type: str,
    occurrence_id: str,
    *,
    context: dict[str, Any] | None = None,
    caller: str = "oracle",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Compatibility alias for the provider-neutral submission capability."""
    return submit_notification(
        notification_type,
        occurrence_id,
        context=context,
        caller=caller,
        correlation_id=correlation_id,
    )


def build_notification_delivery_decisions(source: str | None) -> dict[str, str]:
    """Compatibility adapter for satellite alert polling."""
    return build_satellite_delivery_decisions(source, now=_now_local())


def _result(
    notification_type: str,
    occurrence_id: str,
    *,
    status: str,
    queued_targets: list[str] | None = None,
    channel_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "notification_id": notification_type,
        "event_id": occurrence_id,
        "queued_targets": list(queued_targets or []),
        "channel_results": dict(channel_results or {}),
    }


def _now_local():
    from oracle_app.alerts import _now_local as alerts_now_local

    return alerts_now_local()
