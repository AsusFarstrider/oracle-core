from __future__ import annotations

from typing import Any

from oracle_app.config import get_notification_settings

from .errors import NotificationDefinitionNotFoundError


def resolve_notification_definition(
    notification_type: str,
    *,
    settings: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    resolved_settings = settings or get_notification_settings()
    clean_type = str(notification_type or "").strip().lower()
    definition = (resolved_settings.get("notifications") or {}).get(clean_type)
    if not isinstance(definition, dict) or definition.get("enabled") is not True:
        raise NotificationDefinitionNotFoundError(clean_type)
    return clean_type, definition, resolved_settings
