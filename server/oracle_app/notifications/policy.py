from __future__ import annotations

from typing import Any, Literal

from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge


SuppressionStatus = Literal["active", "inactive", "unavailable"]


def evaluate_notification_suppression(
    definition: dict[str, Any],
    *,
    settings: dict[str, Any],
    home_assistant_base_url: str,
    home_assistant_token: str,
) -> SuppressionStatus:
    mode_ids = [str(value or "").strip().lower() for value in definition.get("suppressed_by") or []]
    if not mode_ids:
        return "inactive"

    modes = settings.get("modes") or {}
    bridge = HomeAssistantBridge(base_url=home_assistant_base_url, token=home_assistant_token)
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
