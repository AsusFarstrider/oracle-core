from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oracle_app.config import get_apprise_settings, get_notification_settings
from oracle_app.provider_bridges.apprise import AppriseBridge, AppriseBridgeError

from .receipts import (
    list_due_notification_deliveries,
    list_expired_notification_deliveries,
    transition_notification_delivery,
)
from .policy import evaluate_notification_suppression

if TYPE_CHECKING:
    from .canonical import CanonicalNotificationExecution


logger = logging.getLogger("oracle-brain.notifications.external-worker")


def process_due_external_deliveries(
    *,
    now: datetime | None = None,
    db_path: Path | None = None,
    notification_settings: dict[str, Any] | None = None,
    apprise_settings: dict[str, Any] | None = None,
    bridge: AppriseBridge | None = None,
    suppression_evaluator=evaluate_notification_suppression,
    canonical_execution: CanonicalNotificationExecution | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if canonical_execution is not None and (
        notification_settings is not None or apprise_settings is not None
    ):
        raise ValueError(
            "Canonical external notification delivery cannot accept legacy settings."
        )
    current = now or datetime.now(UTC)
    current_iso = current.isoformat()
    outcomes: list[dict[str, Any]] = []
    for receipt in list_expired_notification_deliveries(
        now=current_iso,
        channel="external",
        limit=limit,
        db_path=db_path,
    ):
        updated = transition_notification_delivery(
            str(receipt["receipt_id"]),
            status="expired",
            last_error_code="delivery_expired",
            db_path=db_path,
        )
        outcomes.append(_outcome(updated))

    remaining = limit - len(outcomes)
    if remaining <= 0:
        return outcomes
    due = list_due_notification_deliveries(
        now=current_iso,
        channel="external",
        limit=remaining,
        db_path=db_path,
    )
    if not due:
        return outcomes
    resolved_notifications = (
        None
        if canonical_execution is not None
        else (notification_settings or get_notification_settings())
    )
    resolved_apprise = (
        None if canonical_execution is not None else (apprise_settings or get_apprise_settings())
    )
    resolved_bridge = bridge or AppriseBridge()
    for receipt in due:
        receipt_id = str(receipt["receipt_id"])
        if receipt.get("status") == "retry_wait":
            receipt = transition_notification_delivery(
                receipt_id,
                status="pending",
                db_path=db_path,
            )
        try:
            if canonical_execution is not None:
                definition, group = _resolve_canonical_delivery(receipt, canonical_execution)
                suppression = canonical_execution.evaluate_suppression(definition)
            else:
                assert resolved_notifications is not None
                definition, group = _resolve_delivery(receipt, resolved_notifications)
                suppression = suppression_evaluator(
                    definition,
                    settings=resolved_notifications,
                )
            if suppression == "active":
                updated = transition_notification_delivery(
                    receipt_id,
                    status="suppressed",
                    last_error_code="suppressed_before_external_delivery",
                    db_path=db_path,
                )
                outcomes.append(_outcome(updated))
                continue
            if suppression != "inactive":
                expires_at = _parse_timestamp(receipt.get("expires_at"))
                retry_at = current + timedelta(
                    seconds=int(receipt.get("retry_seconds") or 30)
                )
                if expires_at is not None and retry_at > expires_at:
                    retry_at = expires_at
                updated = transition_notification_delivery(
                    receipt_id,
                    status="retry_wait",
                    next_attempt_at=retry_at.isoformat(),
                    last_error_code="suppression_unavailable",
                    db_path=db_path,
                )
                outcomes.append(_outcome(updated))
                continue
            if canonical_execution is not None:
                result = resolved_bridge.send_to(
                    base_url=group.provider.resolved_base_url,
                    timeout_seconds=group.provider.timeout_seconds,
                    config_key=group.definition.configuration_key,
                    routing_tag=group.definition.routing_tag,
                    title="Oracle",
                    body=definition.definition.message,
                    notification_type="info",
                    body_format="text",
                )
            else:
                assert resolved_apprise is not None
                result = resolved_bridge.send(
                    settings=resolved_apprise,
                    config_key=str(group["config_key"]),
                    routing_tag=str(group["routing_tag"]),
                    title="Oracle",
                    body=str(definition["message"]),
                    notification_type="info",
                    body_format="text",
                )
        except AppriseBridgeError as exc:
            updated = _record_provider_failure(
                receipt,
                exc,
                now=current,
                db_path=db_path,
            )
        except (KeyError, ValueError) as exc:
            updated = transition_notification_delivery(
                receipt_id,
                status="failed",
                last_error_class=type(exc).__name__,
                last_error_code="invalid_external_delivery_config",
                db_path=db_path,
            )
        else:
            if str(result.get("status") or "") != "accepted":
                updated = _record_provider_failure(
                    receipt,
                    RuntimeError("Apprise did not accept the request."),
                    now=current,
                    db_path=db_path,
                )
            else:
                updated = transition_notification_delivery(
                    receipt_id,
                    status="accepted",
                    attempted=True,
                    db_path=db_path,
                )
        outcomes.append(_outcome(updated))
    return outcomes


async def external_delivery_worker_loop(
    *,
    poll_seconds: float = 5.0,
    canonical_execution: CanonicalNotificationExecution | None = None,
) -> None:
    while True:
        try:
            await asyncio.to_thread(
                process_due_external_deliveries,
                canonical_execution=canonical_execution,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("external_notification_worker_failed")
        await asyncio.sleep(max(0.25, poll_seconds))


def _resolve_delivery(
    receipt: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(receipt.get("provider") or "") != "apprise":
        raise ValueError("External receipt provider is not apprise.")
    notification_type = str(receipt.get("notification_type") or "")
    definition = (settings.get("notifications") or {}).get(notification_type)
    if not isinstance(definition, dict) or definition.get("enabled") is not True:
        raise ValueError("Notification definition is unavailable.")
    external = definition.get("external_delivery")
    if not isinstance(external, dict) or external.get("enabled") is not True:
        raise ValueError("External delivery is disabled.")
    destination_id = str(receipt.get("destination_id") or "")
    if destination_id not in (external.get("recipient_groups") or []):
        raise ValueError("Receipt destination is not enabled for this notification.")
    group = (settings.get("recipient_groups") or {}).get(destination_id)
    if not isinstance(group, dict) or group.get("enabled") is not True:
        raise ValueError("Recipient group is unavailable.")
    if str(group.get("provider") or "") != "apprise":
        raise ValueError("Recipient group provider is not apprise.")
    return definition, group


def _resolve_canonical_delivery(
    receipt: dict[str, Any],
    execution: CanonicalNotificationExecution,
):
    if str(receipt.get("provider") or "") != "apprise":
        raise ValueError("External receipt provider is not apprise.")
    settings = execution.settings
    if settings is None or not settings.enabled:
        raise ValueError("Notification configuration is unavailable.")
    notification_type = str(receipt.get("notification_type") or "")
    definition = settings.notification_type(notification_type)
    if definition is None:
        raise ValueError("Notification definition is unavailable.")
    external = definition.definition.external_delivery
    if external is None or not external.enabled:
        raise ValueError("External delivery is disabled.")
    destination_id = str(receipt.get("destination_id") or "")
    group = definition.external_recipient_groups.get(destination_id)
    if group is None or group.provider.type != "apprise":
        raise ValueError("Recipient group is unavailable.")
    return definition, group


def _record_provider_failure(
    receipt: dict[str, Any],
    exc: Exception,
    *,
    now: datetime,
    db_path: Path | None,
) -> dict[str, Any]:
    attempted_count = int(receipt.get("attempt_count") or 0) + 1
    max_attempts = int(receipt.get("max_attempts") or 1)
    retry_seconds = int(receipt.get("retry_seconds") or 30)
    next_attempt = now + timedelta(seconds=retry_seconds)
    expires_at = _parse_timestamp(receipt.get("expires_at"))
    retryable = bool(getattr(exc, "retryable", False))
    can_retry = retryable and attempted_count < max_attempts and (
        expires_at is not None and next_attempt < expires_at
    )
    return transition_notification_delivery(
        str(receipt["receipt_id"]),
        status="retry_wait" if can_retry else "failed",
        attempted=True,
        next_attempt_at=next_attempt.isoformat() if can_retry else None,
        last_error_class=type(exc).__name__,
        last_error_code=(
            f"http_{getattr(exc, 'status_code')}"
            if getattr(exc, "status_code", None) is not None
            else "provider_unavailable" if retryable else "provider_rejected"
        ),
        db_path=db_path,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _outcome(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "receipt_id": str(receipt.get("receipt_id") or ""),
        "status": str(receipt.get("status") or ""),
        "attempt_count": int(receipt.get("attempt_count") or 0),
    }
