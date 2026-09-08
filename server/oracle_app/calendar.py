from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .calendar_models import CalendarEvent
from .calendar_write import parse_calendar_write_request


WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class CalendarQuery:
    intent: str
    start: datetime | None
    end: datetime | None
    search_text: str | None
    original_text: str
    person_id: str | None = None
    location_text: str | None = None
    anchor_text: str | None = None
    force_refresh: bool = False
    calendar_id: str | None = None


def is_calendar_request(
    text: str, *, timezone_name: str = "UTC", person_id: str | None = None,
    calendar_id: str | None = None,
) -> bool:
    return (
        parse_calendar_query(
            text, timezone_name=timezone_name, person_id=person_id, calendar_id=calendar_id
        ) is not None
        or parse_calendar_write_request(text) is not None
    )


def parse_calendar_query(
    text: str,
    *,
    timezone_name: str = "UTC",
    now: datetime | None = None,
    person_id: str | None = None,
    calendar_id: str | None = None,
) -> CalendarQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    reference = now or _now_in_calendar_timezone(timezone_name)
    force_refresh = any(
        phrase in normalized
        for phrase in ("refresh", "check again", "calendar again", "up to date", "updated")
    )

    if re.search(r"\b(?:delete|remove|edit|update|reschedule|move)\b", normalized) and any(
        token in normalized for token in ("calendar", "event", "appointment")
    ):
        return CalendarQuery(
            "unsupported_mutation", None, None, None, normalized,
            person_id=person_id, calendar_id=calendar_id,
        )

    location_match = re.match(r"^when do i need to be in (.+)$", normalized)
    if location_match:
        return CalendarQuery(
            intent="find_location", start=None, end=None, search_text=None,
            original_text=normalized, person_id=person_id,
            location_text=location_match.group(1).strip(" ?.!") or None,
            force_refresh=force_refresh,
            calendar_id=calendar_id,
        )

    when_match = re.match(r"^(?:when is|when's) (.+)$", normalized)
    if when_match:
        search_text = when_match.group(1).strip(" ?.!")
        if search_text:
            return CalendarQuery(
                intent="find_event",
                start=None,
                end=None,
                search_text=search_text,
                original_text=normalized,
                person_id=person_id,
                force_refresh=force_refresh,
                calendar_id=calendar_id,
            )

    if re.fullmatch(r"(?:what(?:'s| is) )?next on (?:my|the) calendar", normalized):
        return CalendarQuery(
            "next_event", None, None, None, normalized,
            person_id=person_id, force_refresh=force_refresh,
            calendar_id=calendar_id,
        )

    free_busy = bool(re.match(r"^(?:am i|is .+|are .+) (?:free|busy)\b", normalized))
    natural_list = bool(
        re.match(
            r"^(?:what(?:'s| is) going on|what am i doing|anything|what do i have)\b",
            normalized,
        )
    )
    named_person_list = bool(
        re.match(r"^(?:what (?:is|are) .+ doing|does .+ have anything)\b", normalized)
        and any(token in normalized for token in ("today", "tomorrow", "week", *WEEKDAY_NAMES))
    )
    if named_person_list and person_id is None:
        return None
    has_calendar_word = any(token in normalized for token in ("calendar", "schedule", "agenda"))
    calendar_question = normalized in {"calendar", "schedule", "agenda"} or normalized.startswith(
        ("what", "when", "show", "check", "refresh", "anything", "do i", "am i", "is ", "are ")
    )
    has_calendar_question = has_calendar_word and calendar_question
    if not has_calendar_question and not normalized.startswith("do i have") and not natural_list and not named_person_list and not free_busy:
        return None

    start, end = _infer_range(normalized, reference)
    anchor_text = _after_event_anchor(normalized)
    return CalendarQuery(
        intent="availability" if free_busy else "after_event" if anchor_text else "list_events",
        start=start,
        end=end,
        search_text=None,
        original_text=normalized,
        person_id=person_id,
        anchor_text=anchor_text,
        force_refresh=force_refresh,
        calendar_id=calendar_id,
    )


def check_calendar_health(*, canonical_execution=None) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.health()
    return {
        "status": "disabled",
        "service": "oracle-brain",
        "calendar_configured": False,
        "timezone": "UTC",
        "detail": "Calendar feed is not configured",
    }


def _list_events(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    start = query.start
    end = query.end
    filtered = [
        event
        for event in events
        if start is not None and end is not None and event.end > start and event.start < end
    ]
    if _should_omit_ended_events_for_today(query, timezone_name):
        now = _now_in_calendar_timezone(timezone_name)
        filtered = [event for event in filtered if event.end > now]
    filtered.sort(key=lambda item: item.start)
    return {
        "action": "list_events",
        "query": {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "original_text": query.original_text,
        },
        "events": [_event_to_payload(event, timezone_name) for event in filtered],
    }


def _next_event(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    now = _now_in_calendar_timezone(timezone_name)
    upcoming = sorted((event for event in events if event.end > now), key=lambda item: item.start)
    return {
        "action": "next_event",
        "query": {"original_text": query.original_text},
        "events": [_event_to_payload(upcoming[0], timezone_name)] if upcoming else [],
    }


def _availability(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    result = _list_events(query, events, timezone_name)
    return {**result, "action": "availability", "busy": bool(result["events"])}


def _events_after_anchor(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    anchor_query = CalendarQuery(
        "find_event", query.start, query.end, query.anchor_text, query.original_text,
        person_id=query.person_id,
    )
    anchor_result = _find_matching_event(anchor_query, events, timezone_name, within_range=True)
    anchors = anchor_result.get("events") or []
    if anchor_result.get("ambiguous"):
        return {**anchor_result, "action": "after_event", "anchor_text": query.anchor_text}
    if not anchors:
        return {
            "action": "after_event", "anchor_text": query.anchor_text,
            "events": [], "anchor_not_found": True,
        }
    anchor_end = datetime.fromisoformat(anchors[0]["end"])
    narrowed = CalendarQuery(
        "list_events", anchor_end, query.end, None, query.original_text,
        person_id=query.person_id,
    )
    result = _list_events(narrowed, events, timezone_name)
    return {**result, "action": "after_event", "anchor": anchors[0]}


def _should_omit_ended_events_for_today(query: CalendarQuery, timezone_name: str) -> bool:
    if query.start is None or query.end is None:
        return False
    now = _now_in_calendar_timezone(timezone_name)
    today_start = datetime.combine(now.date(), time.min, tzinfo=ZoneInfo(timezone_name))
    tomorrow_start = today_start + timedelta(days=1)
    if query.start != today_start or query.end != tomorrow_start:
        return False
    return not _is_full_day_request(query.original_text)


def _is_full_day_request(text: str) -> bool:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return False
    full_day_markers = (
        "full calendar",
        "full day",
        "everything today",
        "all day",
        "whole day",
    )
    return any(marker in normalized for marker in full_day_markers)


def _find_matching_event(
    query: CalendarQuery,
    events: list[CalendarEvent],
    timezone_name: str,
    *,
    within_range: bool = False,
) -> dict[str, Any]:
    target = _normalize_search_text(query.search_text or "")
    target_tokens = _search_tokens(target)
    matched = []
    now = _now_in_calendar_timezone(timezone_name)
    for event in events:
        if within_range and query.start is not None and query.end is not None:
            if event.end <= query.start or event.start >= query.end:
                continue
        summary = _normalize_search_text(event.summary)
        if not summary:
            continue
        summary_tokens = _search_tokens(summary)
        score = _calendar_match_score(target, target_tokens, summary, summary_tokens)
        if score:
            matched.append((score, event))

    matched.sort(key=lambda item: _calendar_match_sort_key(item[0], item[1], now))
    if not matched:
        return {
            "action": "find_event",
            "query": query.search_text,
            "events": [],
            "not_found": True,
        }
    top = matched[0][1]
    top_score = matched[0][0]
    top_matches = [event for score, event in matched if score == top_score]
    future_top_matches = [event for event in top_matches if event.end > now]
    ambiguity_candidates = future_top_matches or top_matches
    top_summaries = {
        _normalize_search_text(event.summary)
        for event in ambiguity_candidates
    }
    if len(top_summaries) > 1:
        return {
            "action": "find_event", "query": query.search_text,
            "events": [
                _event_to_payload(event, timezone_name)
                for event in ambiguity_candidates[:3]
            ],
            "ambiguous": True,
        }
    return {
        "action": "find_event",
        "query": query.search_text,
        "events": [_event_to_payload(top, timezone_name)],
    }


def _find_location(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    target = _normalize_search_text(query.location_text or "")
    now = _now_in_calendar_timezone(timezone_name)
    matches = sorted(
        (
            event for event in events
            if target and target in _normalize_search_text(event.location) and event.end > now
        ),
        key=lambda item: item.start,
    )
    return {
        "action": "find_location", "query": query.location_text,
        "events": [_event_to_payload(matches[0], timezone_name)] if matches else [],
        "not_found": not matches,
    }


def _calendar_match_score(
    target: str,
    target_tokens: list[str],
    summary: str,
    summary_tokens: list[str],
) -> int:
    if summary == target:
        return 100
    if target and target in summary:
        return 80
    if not target_tokens:
        return 0
    target_set = set(target_tokens)
    summary_set = set(summary_tokens)
    overlap = len(target_set & summary_set)
    if overlap == 0:
        return 0
    if target_set.issubset(summary_set):
        return 72 + min(len(target_set), 4)
    minimum_overlap = 1 if len(target_tokens) == 1 else max(2, (len(target_tokens) + 1) // 2)
    if overlap < minimum_overlap:
        return 0
    return 40 + min(overlap, 4)


def _calendar_match_sort_key(score: int, event: CalendarEvent, now: datetime) -> tuple[int, int, timedelta]:
    is_past = 1 if event.start < now else 0
    distance = abs(event.start - now)
    return (-score, is_past, distance)


def _event_to_payload(event: CalendarEvent, timezone_name: str) -> dict[str, Any]:
    return {
        "uid": event.uid,
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": event.all_day,
        "location": event.location,
        "timezone": timezone_name,
        "source_id": event.source_id,
        "source_label": event.source_label,
        "user_ids": list(event.user_ids),
    }


def _normalize_search_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", " ".join(value.strip().lower().split()))


def _search_tokens(value: str) -> list[str]:
    stopwords = {"a", "an", "the", "my", "on", "at", "for", "to", "of"}
    return [token for token in value.split(" ") if token and token not in stopwords]


def _now_in_calendar_timezone(timezone_name: str = "UTC") -> datetime:
    timezone = ZoneInfo(timezone_name)
    return datetime.now(timezone)


def _infer_range(text: str, now: datetime) -> tuple[datetime, datetime]:
    day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    if "tomorrow" in text:
        start = day_start + timedelta(days=1)
        end = start + timedelta(days=1)
    elif "this weekend" in text:
        days_until_saturday = (5 - now.weekday()) % 7
        start = day_start + timedelta(days=days_until_saturday)
        end = start + timedelta(days=2)
    elif "next week" in text:
        days_until_monday = (7 - now.weekday()) % 7
        days_until_monday = days_until_monday or 7
        start = day_start + timedelta(days=days_until_monday)
        end = start + timedelta(days=7)
    elif "this week" in text:
        start = day_start
        end = start + timedelta(days=7 - now.weekday())
    else:
        weekday = _extract_weekday(text)
        if weekday is not None:
            days_until = (weekday - now.weekday()) % 7
            start = day_start + timedelta(days=days_until)
            end = start + timedelta(days=1)
        else:
            start = day_start
            end = start + timedelta(days=1)

    after_clock = _after_clock(text)
    if after_clock is not None:
        start = max(start, datetime.combine(start.date(), after_clock, tzinfo=now.tzinfo))
    elif "afternoon" in text:
        start = max(start, datetime.combine(start.date(), time(12, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(17, 0), tzinfo=now.tzinfo))
    elif "morning" in text:
        start = max(start, datetime.combine(start.date(), time(6, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(12, 0), tzinfo=now.tzinfo))
    elif "evening" in text or "tonight" in text:
        start = max(start, datetime.combine(start.date(), time(17, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(23, 59), tzinfo=now.tzinfo))

    return start, end


def _after_clock(text: str) -> time | None:
    match = re.search(r"\bafter\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", text)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")
    if hour > 23 or minute > 59 or (meridiem and not 1 <= hour <= 12):
        return None
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and 1 <= hour <= 7:
        hour += 12
    return time(hour, minute)


def _after_event_anchor(text: str) -> str | None:
    match = re.search(r"\bafter\s+([a-z][a-z0-9 '&.-]{1,60})", text)
    if match is None or _after_clock(text) is not None:
        return None
    anchor = re.split(r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", match.group(1))[0]
    return anchor.strip(" ?.!") or None


def _extract_weekday(text: str) -> int | None:
    for name, index in WEEKDAY_NAMES.items():
        if name in text:
            return index
    return None
