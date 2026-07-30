from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from zoneinfo import ZoneInfo

from .provider_bridges import CalendarBridgeError, get_calendar_bridge


WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTH_PATTERN = (
    r"january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
    r"august|aug|september|sep|sept|october|oct|november|nov|december|dec"
)
_DATE_RE = re.compile(
    rf"\b(?P<phrase>(?:(?:on|for)\s+)?(?:{_MONTH_PATTERN})\s+\d{{1,2}}(?:,\s*\d{{4}}|\s+\d{{4}})?)\b"
)
_NEXT_WEEKDAY_RE = re.compile(r"\b(?P<phrase>next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b")
_WEEKDAY_RE = re.compile(r"\b(?P<phrase>(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b")
_RELATIVE_RE = re.compile(r"\b(?P<phrase>today|tomorrow)\b")
_TIME_RE = re.compile(r"\bat\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b")
_UNTIL_RE = re.compile(r"\buntil\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b")
_FOR_DURATION_RE = re.compile(
    r"\bfor\s+(?P<value>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b"
)


@dataclass(frozen=True)
class CalendarWriteIntent:
    title: str | None
    date: str | None
    start_time: str | None
    end_time: str | None
    duration_minutes: int | None
    original_text: str


def parse_calendar_write_request(text: str, *, now: datetime | None = None, timezone_name: str = "UTC") -> CalendarWriteIntent | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None
    if not _looks_like_calendar_write_request(normalized):
        return None

    working = normalized
    for prefix in ("add ", "schedule ", "create ", "put "):
        if working.startswith(prefix):
            working = working[len(prefix) :].strip()
            break
    working = re.sub(r"\b(?:to|on|in)\s+my\s+calendar\b", "", working).strip()
    working = re.sub(r"\bcalendar event\b", "", working).strip()
    working = re.sub(r"\bmy calendar\b", "", working).strip()

    reference = now or datetime.now(ZoneInfo(timezone_name))
    date_value, working = _extract_date_value(working, now=reference)
    start_time, working, time_ambiguous = _extract_start_time(working)
    end_time, duration_minutes, working = _extract_end_or_duration(working, start_time=start_time)
    title = _extract_title(working)

    if time_ambiguous and start_time is None:
        return CalendarWriteIntent(
            title=title,
            date=date_value,
            start_time=None,
            end_time=end_time,
            duration_minutes=duration_minutes,
            original_text=normalized,
        )

    return CalendarWriteIntent(
        title=title,
        date=date_value,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration_minutes,
        original_text=normalized,
    )


def build_or_continue_event_draft(
    text: str,
    *,
    pending: dict[str, Any] | None = None,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> tuple[str, dict[str, Any]]:
    reference = now or datetime.now(ZoneInfo(timezone_name))
    if pending is None:
        parsed = parse_calendar_write_request(text, now=reference, timezone_name=timezone_name)
        if parsed is None:
            raise ValueError("calendar_write_unrecognized")
        collected = {
            "title": parsed.title,
            "date": parsed.date,
            "start_time": parsed.start_time,
            "end_time": parsed.end_time,
            "duration_minutes": parsed.duration_minutes,
        }
        return _advance_calendar_write_flow(
            collected=collected,
            source_text=text,
            timezone_name=timezone_name,
        )

    collected = dict(pending.get("collected") or {})
    missing_field = str(pending.get("missing_field") or "").strip()
    updated = _apply_clarification_reply(
        text,
        collected=collected,
        missing_field=missing_field,
        now=reference,
        timezone_name=timezone_name,
    )
    if updated is None:
        return (
            "clarification",
            {
                "prompt": _steer_back_prompt(missing_field),
                "collected": collected,
                "missing_field": missing_field,
                "source_text": str(pending.get("source_text") or ""),
            },
        )
    return _advance_calendar_write_flow(
        collected=updated,
        source_text=str(pending.get("source_text") or text),
        timezone_name=timezone_name,
    )


def build_confirmation_prompt(event_draft: dict[str, Any]) -> str:
    title = str(event_draft.get("title") or "").strip()
    date_value = str(event_draft.get("date") or "").strip()
    all_day = bool(event_draft.get("all_day"))
    start_time = str(event_draft.get("start_time") or "").strip()
    end_time = str(event_draft.get("end_time") or "").strip()
    if not title or not date_value:
        raise ValueError("incomplete_event_draft")
    day_text = _format_confirmation_date(date_value)
    if all_day:
        return f"I've got '{title}' on {day_text} as an all-day event. Do you want me to add it?"
    if not (start_time and end_time):
        raise ValueError("incomplete_event_draft")
    return (
        f"I've got '{title}' on {day_text} from "
        f"{_format_confirmation_time(start_time)} to {_format_confirmation_time(end_time)}. "
        "Do you want me to add it?"
    )


def commit_calendar_event(event_draft: dict[str, Any], *, settings: dict[str, Any]) -> dict[str, Any]:
    try:
        bridge = get_calendar_bridge(settings)
        committed = bridge.commit_event(event_draft, settings=settings)
        from .calendar import invalidate_calendar_cache

        invalidate_calendar_cache()
        return committed
    except CalendarBridgeError as exc:
        raise RuntimeError(exc.detail) from exc


def build_event_draft_from_collected(collected: dict[str, Any]) -> dict[str, Any] | None:
    title = str(collected.get("title") or "").strip()
    date_value = str(collected.get("date") or "").strip()
    start_time = str(collected.get("start_time") or "").strip()
    end_time = str(collected.get("end_time") or "").strip()
    if not (title and date_value and start_time and end_time):
        return None
    return {
        "title": title,
        "date": date_value,
        "start_time": start_time,
        "end_time": end_time,
    }


def _advance_calendar_write_flow(
    *,
    collected: dict[str, Any],
    source_text: str,
    timezone_name: str,
) -> tuple[str, dict[str, Any]]:
    normalized = _normalize_collected(collected)
    missing_field = _first_missing_field(normalized)
    if missing_field is not None:
        return (
            "clarification",
            {
                "prompt": _prompt_for_missing_field(missing_field),
                "collected": normalized,
                "missing_field": missing_field,
                "source_text": source_text,
            },
        )
    event_draft = build_event_draft_from_collected(normalized)
    if event_draft is None:
        raise ValueError("calendar_write_incomplete")
    return (
        "confirmation",
        {
            "event_draft": event_draft,
            "prompt": build_confirmation_prompt(event_draft),
            "source_text": source_text,
            "timezone": timezone_name,
        },
    )


def _apply_clarification_reply(
    text: str,
    *,
    collected: dict[str, Any],
    missing_field: str,
    now: datetime,
    timezone_name: str,
) -> dict[str, Any] | None:
    normalized = " ".join(str(text).strip().lower().split())
    updated = dict(collected)
    if missing_field == "title":
        if _looks_like_field_only_date(normalized, now=now) or _looks_like_field_only_time(normalized):
            return None
        title = _extract_title(normalized)
        if not title:
            return None
        updated["title"] = title
        return updated
    if missing_field == "date":
        date_value, _ = _extract_date_value(normalized, now=now)
        if not date_value:
            return None
        updated["date"] = date_value
        return updated
    if missing_field == "start_time":
        start_time, _, ambiguous = _extract_start_time(f"at {normalized}")
        if ambiguous or not start_time:
            return None
        updated["start_time"] = start_time
        if updated.get("duration_minutes") and not updated.get("end_time"):
            updated["end_time"] = _add_minutes_to_time(start_time, int(updated["duration_minutes"]))
        return updated
    if missing_field == "end_time":
        end_time, duration_minutes, _ = _extract_end_or_duration(normalized, start_time=str(updated.get("start_time") or ""))
        if end_time:
            updated["end_time"] = end_time
            updated["duration_minutes"] = duration_minutes
            return updated
        return None
    return None


def _normalize_collected(collected: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "title": _extract_title(str(collected.get("title") or "")),
        "date": str(collected.get("date") or "").strip() or None,
        "start_time": str(collected.get("start_time") or "").strip() or None,
        "end_time": str(collected.get("end_time") or "").strip() or None,
        "duration_minutes": int(collected["duration_minutes"]) if collected.get("duration_minutes") else None,
    }
    if normalized["start_time"] and normalized["duration_minutes"] and not normalized["end_time"]:
        normalized["end_time"] = _add_minutes_to_time(normalized["start_time"], int(normalized["duration_minutes"]))
    return normalized


def _first_missing_field(collected: dict[str, Any]) -> str | None:
    if not collected.get("title"):
        return "title"
    if not collected.get("date"):
        return "date"
    if not collected.get("start_time"):
        return "start_time"
    if not collected.get("end_time"):
        return "end_time"
    return None


def _prompt_for_missing_field(field_name: str) -> str:
    if field_name == "title":
        return "What should I call the event?"
    if field_name == "date":
        return "What day is that for?"
    if field_name == "start_time":
        return "What time should it start?"
    return "How long should it last?"


def _steer_back_prompt(field_name: str) -> str:
    if field_name == "title":
        return "Let's finish the event first. What should I call the event?"
    if field_name == "date":
        return "Let's finish the event first. What day is that for?"
    if field_name == "start_time":
        return "Let's finish the event first. What time should it start?"
    return "Let's finish the event first. How long should it last?"


def _looks_like_calendar_write_request(text: str) -> bool:
    if not text:
        return False
    prefixes = ("add ", "schedule ", "create ", "put ")
    if not any(text.startswith(prefix) for prefix in prefixes):
        return False
    return "calendar" in text


def _extract_title(text: str) -> str | None:
    cleaned = " ".join(str(text).strip().split())
    cleaned = re.sub(r"^(?:to|for|on)\s+", "", cleaned).strip()
    cleaned = re.sub(r"\b(?:to|on|in)\s+my\s+calendar\b", "", cleaned).strip()
    cleaned = re.sub(r"\bcalendar event\b", "", cleaned).strip()
    cleaned = re.sub(r"\bmy calendar\b", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .?!,")
    return cleaned or None


def _extract_date_value(text: str, *, now: datetime) -> tuple[str | None, str]:
    working = text
    match = _RELATIVE_RE.search(working)
    if match:
        phrase = match.group("phrase")
        if phrase == "today":
            parsed_date = now.date()
        else:
            parsed_date = now.date() + timedelta(days=1)
        working = _remove_span(working, match.span("phrase"))
        return parsed_date.isoformat(), working.strip()

    match = _NEXT_WEEKDAY_RE.search(working)
    if match:
        weekday_name = match.group("phrase").split()[-1]
        parsed_date = _resolve_next_weekday(now.date(), weekday_name, force_next_week=True)
        working = _remove_span(working, match.span("phrase"))
        return parsed_date.isoformat(), working.strip()

    match = _WEEKDAY_RE.search(working)
    if match:
        weekday_name = match.group("phrase")
        parsed_date = _resolve_next_weekday(now.date(), weekday_name, force_next_week=False)
        working = _remove_span(working, match.span("phrase"))
        return parsed_date.isoformat(), working.strip()

    match = _DATE_RE.search(working)
    if match:
        phrase = re.sub(r"^(?:on|for)\s+", "", match.group("phrase")).replace(",", " ").strip()
        parsed = _parse_month_day_phrase(phrase, now=now.date())
        if parsed is not None:
            working = _remove_span(working, match.span("phrase"))
            return parsed.isoformat(), working.strip()

    return None, text


def _extract_start_time(text: str) -> tuple[str | None, str, bool]:
    match = _TIME_RE.search(text)
    if not match:
        return None, text, False
    raw_time = match.group("time").strip()
    parsed, ambiguous = _parse_time_value(raw_time)
    working = _remove_span(text, match.span())
    return parsed, working.strip(), ambiguous


def _extract_end_or_duration(text: str, *, start_time: str | None) -> tuple[str | None, int | None, str]:
    match = _UNTIL_RE.search(text)
    if match:
        parsed, ambiguous = _parse_time_value(match.group("time").strip())
        if parsed is not None and not ambiguous:
            working = _remove_span(text, match.span())
            return parsed, None, working.strip()
    match = _FOR_DURATION_RE.search(text)
    if match:
        value = int(match.group("value"))
        unit = match.group("unit").lower()
        minutes = value * 60 if unit.startswith(("hour", "hr")) else value
        working = _remove_span(text, match.span())
        if start_time:
            return _add_minutes_to_time(start_time, minutes), minutes, working.strip()
        return None, minutes, working.strip()
    return None, None, text


def _parse_time_value(value: str) -> tuple[str | None, bool]:
    normalized = " ".join(value.strip().lower().split())
    match = re.fullmatch(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?", normalized)
    if not match:
        return None, False
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem")
    if minute > 59 or hour > 23 or hour == 0:
        return None, False
    if meridiem is None:
        return None, True
    if meridiem == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}", False


def _parse_month_day_phrase(value: str, *, now: date) -> date | None:
    compact = " ".join(value.strip().split())
    formats = ("%B %d %Y", "%b %d %Y", "%B %d", "%b %d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(compact, fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else now.year
        candidate = date(year, parsed.month, parsed.day)
        if "%Y" not in fmt and candidate < now:
            candidate = date(year + 1, parsed.month, parsed.day)
        return candidate
    return None


def _resolve_next_weekday(reference: date, weekday_name: str, *, force_next_week: bool) -> date:
    target = WEEKDAY_NAMES[weekday_name]
    days_until = (target - reference.weekday()) % 7
    if force_next_week:
        days_until = 7 if days_until == 0 else days_until + 7
    elif days_until == 0:
        days_until = 7
    return reference + timedelta(days=days_until)


def _looks_like_field_only_date(text: str, *, now: datetime) -> bool:
    parsed, _ = _extract_date_value(text, now=now)
    return bool(parsed)


def _looks_like_field_only_time(text: str) -> bool:
    parsed, ambiguous = _parse_time_value(text)
    return bool(parsed or ambiguous)


def _format_confirmation_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return parsed.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _format_confirmation_time(value: str) -> str:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.strftime("%I:%M %p").lstrip("0")


def _add_minutes_to_time(start_time: str, minutes: int) -> str:
    parsed = datetime.strptime(start_time, "%H:%M")
    end = parsed + timedelta(minutes=minutes)
    return end.strftime("%H:%M")


def _remove_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return f"{text[:start]} {text[end:]}".strip()
