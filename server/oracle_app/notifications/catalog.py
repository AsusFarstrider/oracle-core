from __future__ import annotations

from typing import Any

from .errors import NotificationDefinitionNotFoundError


def resolve_notification_definition(
    notification_type: str,
    *,
    settings: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    clean_type = str(notification_type or "").strip().lower()
    definition = (settings.get("notifications") or {}).get(clean_type)
    if not isinstance(definition, dict) or definition.get("enabled") is not True:
        raise NotificationDefinitionNotFoundError(clean_type)
    return clean_type, definition, settings
