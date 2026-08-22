from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from oracle_app.configuration.notification_runtime_settings import NotificationTypeRuntimeSettings

from ..receipts import reserve_notification_delivery


def reserve_external_deliveries(
    *,
    notification_type: str,
    occurrence_id: str,
    correlation_id: str,
    definition: dict[str, Any],
    settings: dict[str, Any],
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    policy = definition.get("external_delivery")
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return _result("disabled")

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    expires_at = current + timedelta(seconds=int(policy["delivery_ttl_seconds"]))
    repeat_policy = str(policy["repeat_policy"])
    if repeat_policy == "first_per_correlation" and not str(correlation_id or "").strip():
        raise ValueError("first_per_correlation external delivery requires correlation_id")

    receipt_ids: list[str] = []
    queued_count = 0
    duplicate_count = 0
    recipient_groups = settings.get("recipient_groups") or {}
    for destination_id in policy.get("recipient_groups") or []:
        group = recipient_groups.get(destination_id)
        if not isinstance(group, dict) or group.get("enabled") is not True:
            raise ValueError(f"External recipient group {destination_id!r} is unavailable")
        receipt, created = reserve_notification_delivery(
            notification_type=notification_type,
            occurrence_id=occurrence_id,
            correlation_id=correlation_id,
            channel="external",
            destination_id=str(destination_id),
            provider=str(group["provider"]),
            max_attempts=int(policy["max_attempts"]),
            retry_seconds=int(policy["retry_seconds"]),
            expires_at=expires_at.isoformat(),
            failure_policy=str(policy["failure_policy"]),
            repeat_policy=repeat_policy,
            db_path=db_path,
        )
        receipt_ids.append(str(receipt["receipt_id"]))
        if created:
            queued_count += 1
        else:
            duplicate_count += 1
    return _result(
        "queued" if queued_count else "duplicate",
        receipt_ids=receipt_ids,
        queued_count=queued_count,
        duplicate_count=duplicate_count,
    )


def reserve_canonical_external_deliveries(
    *,
    notification_type: NotificationTypeRuntimeSettings,
    occurrence_id: str,
    correlation_id: str,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    policy = notification_type.definition.external_delivery
    if policy is None or not policy.enabled:
        return _result("disabled")

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    expires_at = current + timedelta(seconds=policy.delivery_ttl_seconds)
    if policy.repeat_policy == "first_per_correlation" and not str(correlation_id or "").strip():
        raise ValueError("first_per_correlation external delivery requires correlation_id")

    receipt_ids: list[str] = []
    queued_count = 0
    duplicate_count = 0
    for destination_id, group in notification_type.external_recipient_groups.items():
        receipt, created = reserve_notification_delivery(
            notification_type=notification_type.definition.id,
            occurrence_id=occurrence_id,
            correlation_id=correlation_id,
            channel="external",
            destination_id=destination_id,
            provider=group.provider.type,
            max_attempts=policy.max_attempts,
            retry_seconds=policy.retry_seconds,
            expires_at=expires_at.isoformat(),
            failure_policy=policy.failure_policy,
            repeat_policy=policy.repeat_policy,
            db_path=db_path,
        )
        receipt_ids.append(str(receipt["receipt_id"]))
        if created:
            queued_count += 1
        else:
            duplicate_count += 1
    return _result(
        "queued" if queued_count else "duplicate",
        receipt_ids=receipt_ids,
        queued_count=queued_count,
        duplicate_count=duplicate_count,
    )


def _result(
    status: str,
    *,
    receipt_ids: list[str] | None = None,
    queued_count: int = 0,
    duplicate_count: int = 0,
) -> dict[str, Any]:
    return {
        "channel": "external",
        "status": status,
        "receipt_ids": list(receipt_ids or []),
        "queued_count": queued_count,
        "duplicate_count": duplicate_count,
    }
