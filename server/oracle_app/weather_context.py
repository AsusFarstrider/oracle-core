from __future__ import annotations

from dataclasses import dataclass
import re

from .session_state import (
    clear_pending_state,
    get_informational_context,
    get_pending_state,
    set_informational_context,
    set_pending_state,
)
from .weather_forecast import WEEKDAY_NAMES, needs_schedule_anchor


@dataclass(frozen=True)
class WeatherContextResolution:
    action: str
    query: str
    prompt: str | None = None
    context_used: bool = False


def resolve_weather_context(
    text: str,
    action: str,
    *,
    source: str | None,
    session_id: str | None,
) -> WeatherContextResolution:
    pending = get_pending_state(source, session_id, domain="informational")
    if isinstance(pending, dict) and pending.get("target_domain") == "weather":
        original = str(pending.get("original_text") or "").strip()
        anchor = _clock_answer(text)
        if anchor and original:
            clear_pending_state(source, session_id, domain="informational", reason="weather_time_anchor_supplied")
            rewritten = _replace_schedule_anchor(original, anchor)
            return WeatherContextResolution("weather_forecast", rewritten, context_used=True)
        return WeatherContextResolution(
            "weather_forecast",
            text,
            prompt="What time should I use for that weather question?",
        )

    if action in {"weather_forecast", "remote_weather_forecast"} and needs_schedule_anchor(text):
        set_pending_state(
            source,
            session_id,
            pending_type="clarification",
            domain="informational",
            payload={
                "target_domain": "weather",
                "clarification_kind": "weather_time_anchor",
                "prompt": "What time should I use for that weather question?",
                "options": [],
                "original_text": text,
                "subject_id": "weather_time_anchor",
            },
        )
        return WeatherContextResolution(
            action,
            text,
            prompt="What time should I use for that weather question?",
        )

    normalized = _normalize(text)
    if not _is_followup(normalized):
        return WeatherContextResolution(action, text)
    context = get_informational_context(source, session_id, domain="weather")
    subject = context.get("subject") if isinstance(context, dict) else None
    if not isinstance(subject, dict):
        return WeatherContextResolution(action, text)
    query_kind = str(subject.get("query_kind") or "").strip()
    location = str(subject.get("location_label") or "").strip()
    window = _followup_window(normalized)
    if query_kind.startswith("remote") and location:
        return WeatherContextResolution(
            "remote_weather_forecast" if window else "remote_current_weather",
            f"what is the weather{' ' + window if window else ''} in {location}",
            context_used=True,
        )
    if window:
        return WeatherContextResolution(
            "weather_forecast", f"what is the weather {window}", context_used=True
        )
    return WeatherContextResolution(action, text)


def retain_weather_context(
    *,
    action: str,
    query_text: str,
    result: dict[str, object],
    source: str | None,
    session_id: str | None,
) -> bool:
    location = str(result.get("requested_location") or result.get("location") or "home").strip()
    periods = result.get("selected_periods")
    period_items = periods if isinstance(periods, list) else []
    starts = [str(item.get("start_time") or "") for item in period_items if isinstance(item, dict)]
    ends = [str(item.get("end_time") or "") for item in period_items if isinstance(item, dict)]
    source_name = str(result.get("source_name") or "").strip()
    source_type = str(result.get("source_type") or "").strip()
    return set_informational_context(
        source,
        session_id,
        domain="weather",
        subject={
            "subject_id": f"{action}:{location}"[:512],
            "location_id": location.casefold().replace(" ", "_")[:512],
            "location_label": location[:512],
            "window_start": starts[0] if starts else "",
            "window_end": ends[-1] if ends else "",
            "query_kind": action,
            "source_ids": [value for value in (source_name, source_type) if value],
            "evidence_ids": [value for value in (*starts, *ends) if value][:16],
        },
    )


def _clock_answer(text: str) -> str | None:
    normalized = _normalize(text)
    match = re.search(r"\b(?:at|around|by)?\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\b", normalized)
    return match.group(1) if match else None


def _replace_schedule_anchor(text: str, anchor: str) -> str:
    output = text
    for phrase in ("before i leave", "before we leave", "after dinner", "when i leave", "when we leave"):
        output = re.sub(re.escape(phrase), f"at {anchor}", output, flags=re.IGNORECASE)
    return output


def _is_followup(normalized: str) -> bool:
    return normalized.startswith(("what about ", "how about ")) or normalized in {"there", "tomorrow", "tonight"}


def _followup_window(normalized: str) -> str:
    tokens = ("tomorrow", "tonight", "weekend", "morning", "afternoon", "evening", *WEEKDAY_NAMES)
    return next((token for token in tokens if token in normalized), "")


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().strip(" .?!").split())
