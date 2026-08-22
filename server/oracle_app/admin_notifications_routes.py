from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .config import get_apprise_settings, get_notification_settings
from .configuration.notification_runtime_settings import NotificationRuntimeSettings
from .notifications.receipts import (
    DELIVERY_STATUSES,
    NotificationDeliveryQuery,
    list_notification_deliveries,
    summarize_notification_deliveries,
)
from .provider_bridges.apprise import AppriseBridge


def admin_notifications_overview() -> dict[str, Any]:
    notification_settings = get_notification_settings()
    apprise_settings = get_apprise_settings()
    provider = AppriseBridge().check_health(settings=apprise_settings)
    provider["enabled"] = apprise_settings.get("enabled") is True
    definitions = [
        _public_definition(definition)
        for _notification_id, definition in sorted(
            (notification_settings.get("notifications") or {}).items()
        )
        if isinstance(definition, dict)
    ]
    recipient_groups = [
        {
            "id": str(group.get("id") or group_id),
            "enabled": group.get("enabled") is True,
            "provider": str(group.get("provider") or ""),
        }
        for group_id, group in sorted(
            (notification_settings.get("recipient_groups") or {}).items()
        )
        if isinstance(group, dict)
    ]
    recent = list_notification_deliveries(
        NotificationDeliveryQuery(channel="external", limit=25)
    )
    return {
        "ok": True,
        "provider": provider,
        "summary": {
            "notification_count": len(definitions),
            "external_enabled_count": sum(
                1 for item in definitions if item["external_delivery_enabled"] is True
            ),
            "recipient_group_count": len(recipient_groups),
            "enabled_recipient_group_count": sum(
                1 for item in recipient_groups if item["enabled"] is True
            ),
            "deliveries": summarize_notification_deliveries(channel="external"),
        },
        "definitions": definitions,
        "recipient_groups": recipient_groups,
        "recent_deliveries": [_public_delivery(item) for item in recent],
    }


def admin_notification_deliveries(
    notification_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    clean_status = str(status or "").strip().lower() or None
    if clean_status is not None and clean_status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported delivery status.")
    bounded_limit = max(1, min(int(limit), 500))
    bounded_offset = max(0, int(offset))
    rows = list_notification_deliveries(
        NotificationDeliveryQuery(
            notification_type=str(notification_type or "").strip().lower() or None,
            channel="external",
            status=clean_status,
            limit=bounded_limit,
            offset=bounded_offset,
        )
    )
    return {
        "ok": True,
        "summary": summarize_notification_deliveries(channel="external"),
        "filters": {
            "notification_type": str(notification_type or "").strip().lower() or None,
            "status": clean_status,
            "limit": bounded_limit,
            "offset": bounded_offset,
        },
        "deliveries": [_public_delivery(item) for item in rows],
    }


def register_admin_notifications_routes(app: FastAPI) -> None:
    app.get("/api/admin/notifications")(admin_notifications_overview_http)
    app.get("/api/admin/notifications/deliveries")(admin_notification_deliveries)


def admin_notifications_overview_http(request: Request) -> dict[str, Any]:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if isinstance(composition, CanonicalBrainApplicationComposition):
        return _canonical_overview(composition.runtime.notifications)
    raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")


def _canonical_overview(settings: NotificationRuntimeSettings | None) -> dict[str, Any]:
    definitions = []
    recipient_groups = []
    providers = []
    if settings is not None:
        definitions = [
            _public_canonical_definition(runtime)
            for _notification_id, runtime in sorted(settings.types.items())
        ]
        recipient_groups = [
            {
                "id": group.definition.id,
                "enabled": group.definition.enabled,
                "provider": group.provider.type,
            }
            for _group_id, group in sorted(settings.recipient_groups.items())
        ]
        for _provider_id, provider in sorted(settings.providers.items()):
            health = AppriseBridge().check_health_at(
                base_url=provider.resolved_base_url,
                timeout_seconds=provider.timeout_seconds,
            )
            health["enabled"] = True
            health["provider_id"] = provider.provider_id
            providers.append(health)
    provider = providers[0] if providers else {
        "status": "ok",
        "provider": "apprise",
        "configured": False,
        "available": False,
        "enabled": False,
        "detail": "No canonical external notification provider is operational.",
        "missing_config_keys": [],
    }
    recent = list_notification_deliveries(
        NotificationDeliveryQuery(channel="external", limit=25)
    )
    return {
        "ok": True,
        "provider": provider,
        "providers": providers,
        "configuration_revision": None if settings is None else settings.config_revision,
        "summary": {
            "notification_count": len(definitions),
            "external_enabled_count": sum(
                1 for item in definitions if item["external_delivery_enabled"] is True
            ),
            "recipient_group_count": len(recipient_groups),
            "enabled_recipient_group_count": sum(
                1 for item in recipient_groups if item["enabled"] is True
            ),
            "deliveries": summarize_notification_deliveries(channel="external"),
        },
        "definitions": definitions,
        "recipient_groups": recipient_groups,
        "recent_deliveries": [_public_delivery(item) for item in recent],
    }


def _public_definition(definition: dict[str, Any]) -> dict[str, Any]:
    external = definition.get("external_delivery")
    if not isinstance(external, dict):
        external = {}
    return {
        "id": str(definition.get("id") or ""),
        "enabled": definition.get("enabled") is True,
        "external_delivery_enabled": external.get("enabled") is True,
        "recipient_group_count": len(external.get("recipient_groups") or []),
        "max_attempts": int(external.get("max_attempts") or 0),
        "retry_seconds": int(external.get("retry_seconds") or 0),
        "delivery_ttl_seconds": int(external.get("delivery_ttl_seconds") or 0),
        "quiet_hours_policy": str(external.get("quiet_hours_policy") or ""),
        "repeat_policy": str(external.get("repeat_policy") or ""),
        "failure_policy": str(external.get("failure_policy") or ""),
    }


def _public_canonical_definition(runtime) -> dict[str, Any]:
    definition = runtime.definition
    external = definition.external_delivery
    return {
        "id": definition.id,
        "enabled": definition.enabled,
        "external_delivery_enabled": external is not None and external.enabled,
        "recipient_group_count": 0 if external is None else len(external.recipient_groups),
        "max_attempts": 0 if external is None else external.max_attempts,
        "retry_seconds": 0 if external is None else external.retry_seconds,
        "delivery_ttl_seconds": 0 if external is None else external.delivery_ttl_seconds,
        "quiet_hours_policy": "" if external is None else external.quiet_hours_policy,
        "repeat_policy": "" if external is None else external.repeat_policy,
        "failure_policy": "" if external is None else external.failure_policy,
    }


def _public_delivery(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "receipt_id": str(delivery.get("receipt_id") or ""),
        "created_at": str(delivery.get("created_at") or ""),
        "updated_at": str(delivery.get("updated_at") or ""),
        "notification_type": str(delivery.get("notification_type") or ""),
        "occurrence_id": str(delivery.get("occurrence_id") or ""),
        "correlation_id": str(delivery.get("correlation_id") or ""),
        "channel": str(delivery.get("channel") or ""),
        "destination_id": str(delivery.get("destination_id") or ""),
        "provider": str(delivery.get("provider") or ""),
        "status": str(delivery.get("status") or ""),
        "attempt_count": int(delivery.get("attempt_count") or 0),
        "max_attempts": int(delivery.get("max_attempts") or 0),
        "retry_seconds": int(delivery.get("retry_seconds") or 0),
        "next_attempt_at": str(delivery.get("next_attempt_at") or ""),
        "expires_at": str(delivery.get("expires_at") or ""),
        "accepted_at": str(delivery.get("accepted_at") or ""),
        "completed_at": str(delivery.get("completed_at") or ""),
        "failure_policy": str(delivery.get("failure_policy") or ""),
        "repeat_policy": str(delivery.get("repeat_policy") or ""),
        "last_error_class": str(delivery.get("last_error_class") or ""),
        "last_error_code": str(delivery.get("last_error_code") or ""),
    }
