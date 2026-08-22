from __future__ import annotations

from typing import Any, Literal

from oracle_app.config import get_home_assistant_settings, get_notification_settings
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge


SuppressionStatus = Literal["active", "inactive", "unavailable"]


def evaluate_notification_suppression(
    definition: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
) -> SuppressionStatus:
    mode_ids = [str(value or "").strip().lower() for value in definition.get("suppressed_by") or []]
    if not mode_ids:
        return "inactive"

    resolved_settings = settings or get_notification_settings()
    modes = resolved_settings.get("modes") or {}
    try:
        base_url, token = get_home_assistant_settings()
    except Exception:
        return "unavailable"
    bridge = HomeAssistantBridge(base_url=base_url, token=token)
    for mode_id in mode_ids:
        mode = modes.get(mode_id)
        if not isinstance(mode, dict):
            return "unavailable"
        state = bridge.fetch_entity_state(str(mode.get("entity_id") or ""))
        normalized = str((state or {}).get("state") or "").strip().lower()
        if normalized in {"", "unknown", "unavailable"}:
            return "unavailable"
        if normalized == str(mode.get("active_state") or "").strip().lower():
            return "active"
    return "inactive"
