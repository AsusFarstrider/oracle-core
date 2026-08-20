from __future__ import annotations

from dataclasses import dataclass

from .routing_helpers import (
    detect_alert_query,
    detect_date_calculation_query,
    detect_date_query,
    detect_math_query,
    detect_system_cache_refresh,
    detect_system_cancel,
    detect_system_confirm,
    detect_time_query,
    detect_unit_conversion_query,
)
from .user_context import extract_switch_user_name


@dataclass(frozen=True)
class SystemIntent:
    action: str
    reason: str
    confidence: float


def classify_system_intent(normalized_text: str) -> SystemIntent | None:
    if not normalized_text:
        return SystemIntent(
            action="ignore",
            reason="Ignored empty transcript after wake-word cleanup",
            confidence=1.0,
        )

    if detect_system_confirm(normalized_text):
        return SystemIntent(
            action="confirm_pending",
            reason="Matched internal confirmation command",
            confidence=0.99,
        )

    if detect_system_cancel(normalized_text):
        return SystemIntent(
            action="cancel_pending",
            reason="Matched internal cancel command",
            confidence=0.99,
        )

    if detect_system_cache_refresh(normalized_text):
        return SystemIntent(
            action="refresh_cache",
            reason="Matched internal cache refresh command",
            confidence=0.98,
        )

    if extract_switch_user_name(normalized_text):
        return SystemIntent(
            action="switch_user",
            reason="Matched explicit session user switch command",
            confidence=0.98,
        )

    if detect_alert_query(normalized_text):
        return SystemIntent(
            action="alerts",
            reason="Matched timer/alarm/reminder query",
            confidence=0.9,
        )

    if detect_date_calculation_query(normalized_text):
        return SystemIntent(
            action="calculation",
            reason="Matched date calculation query",
            confidence=0.91,
        )

    has_time = detect_time_query(normalized_text)
    has_date = detect_date_query(normalized_text)
    if has_time and has_date:
        return SystemIntent(
            action="current_time_date",
            reason="Matched time/date query",
            confidence=0.9,
        )
    if has_time:
        return SystemIntent(
            action="current_time",
            reason="Matched time query",
            confidence=0.9,
        )
    if has_date:
        return SystemIntent(
            action="current_date",
            reason="Matched date query",
            confidence=0.9,
        )

    if detect_unit_conversion_query(normalized_text):
        return SystemIntent(
            action="calculation",
            reason="Matched unit conversion query",
            confidence=0.92,
        )

    if detect_math_query(normalized_text):
        return SystemIntent(
            action="calculation",
            reason="Matched math query",
            confidence=0.9,
        )

    return None


def build_system_hook(action: str) -> str:
    if action == "ignore":
        return "system.ignore"
    if action == "confirm_pending":
        return "system.confirm_pending"
    if action == "cancel_pending":
        return "system.cancel_pending"
    if action == "current_time_date":
        return "system.current_time_date"
    if action == "current_time":
        return "system.current_time"
    if action == "current_date":
        return "system.current_date"
    if action == "calculation":
        return "system.calculation"
    if action == "alerts":
        return "system.alerts"
    if action == "switch_user":
        return "system.switch_user"
    if action == "refresh_cache":
        return "system.refresh_cache"
    return "system.unknown_operation"


def system_action_requires_text(action: str) -> bool:
    return action in {"calculation", "alerts", "switch_user"}
