from __future__ import annotations

from dataclasses import dataclass
import re

from .routing_helpers import (
    detect_alert_query,
    detect_math_query,
    detect_system_cache_refresh,
    detect_system_cancel,
    detect_system_confirm,
    detect_unit_conversion_query,
)
from .user_context import extract_switch_user_name
from .temporal import parse_temporal_query
from .system_help import classify_help_request


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

    if normalized_text in {
        "repeat that", "repeat", "say that again", "what did you say",
        "can you repeat that", "could you repeat that",
    }:
        return SystemIntent(
            action="repeat",
            reason="Matched session-scoped Repeat request",
            confidence=0.99,
        )

    if classify_help_request(normalized_text) is not None:
        return SystemIntent(
            action="help",
            reason="Matched truthful capability Help request",
            confidence=0.98,
        )

    if normalized_text in {
        "hello", "hi", "hi oracle", "hey oracle", "good morning",
        "good afternoon", "good evening", "thanks", "thank you", "thanks oracle",
    }:
        return SystemIntent(
            action="courtesy",
            reason="Matched bounded greeting or courtesy",
            confidence=0.98,
        )

    if (
        re.fullmatch(r"(?:start|stop|reset|lap|check|show)(?: a| the| my)? stopwatch", normalized_text)
        or re.fullmatch(r"(?:flip|toss)(?: a| the)? coin", normalized_text)
        or re.fullmatch(r"(?:roll)(?: a| the)? (?:die|dice)", normalized_text)
        or re.fullmatch(r"(?:pick|choose|give me) (?:a )?random (?:number|choice)", normalized_text)
        or re.search(r"\b(?:sunrise|sunset)\b", normalized_text)
    ):
        return SystemIntent(
            action="unsupported_utility",
            reason="Matched explicitly deferred deterministic utility",
            confidence=0.98,
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

    temporal = parse_temporal_query(normalized_text)
    if temporal is not None:
        action = temporal.kind if temporal.kind in {"current_time", "current_date", "current_time_date"} else "temporal"
        if action == "current_time_date":
            reason = "Matched time/date query"
        elif action == "current_time":
            reason = "Matched time query"
        elif action == "current_date":
            reason = "Matched date query"
        else:
            reason = "Matched deterministic time/date query"
        return SystemIntent(
            action=action,
            reason=reason,
            confidence=0.9 if action in {"current_time", "current_date", "current_time_date"} else 0.94,
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
    if action == "temporal":
        return "system.temporal"
    if action == "alerts":
        return "system.alerts"
    if action == "switch_user":
        return "system.switch_user"
    if action == "refresh_cache":
        return "system.refresh_cache"
    if action == "repeat":
        return "system.repeat"
    if action == "help":
        return "system.help"
    if action == "courtesy":
        return "system.courtesy"
    if action == "unsupported_utility":
        return "system.unsupported_utility"
    return "system.unknown_operation"


def system_action_requires_text(action: str) -> bool:
    return action in {"calculation", "alerts", "switch_user", "temporal", "help", "courtesy", "unsupported_utility"}
