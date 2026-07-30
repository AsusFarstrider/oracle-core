from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .calendar_models import CalendarEvent
from .config import get_calendar_settings
from .provider_bridges import CalendarBridgeError, get_calendar_bridge
from .calendar_write import parse_calendar_write_request
from .read_cache import BoundedReadCache, CachedRead


_CALENDAR_CACHE: BoundedReadCache[list[CalendarEvent]] = BoundedReadCache()
CALENDAR_TTL_SECONDS = 60
CALENDAR_STALE_MAX_SECONDS = 10 * 60


def invalidate_calendar_cache() -> None:
    _CALENDAR_CACHE.invalidate("calendar:")


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


def load_calendar_events(*, scope: str = "personal") -> list[CalendarEvent]:
    settings = get_calendar_settings()
    return _load_events_for_scope(settings, scope=scope, require_config=False).value


def is_calendar_request(text: str, *, timezone_name: str | None = None) -> bool:
    return (
        parse_calendar_query(text, timezone_name=timezone_name) is not None
        or parse_calendar_write_request(text) is not None
    )


def parse_calendar_query(text: str, *, timezone_name: str | None = None) -> CalendarQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    now = _now_in_calendar_timezone(timezone_name)

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
            )

    if not any(token in normalized for token in ("calendar", "schedule", "agenda")) and not normalized.startswith("do i have"):
        return None

    start, end = _infer_range(normalized, now)
    return CalendarQuery(
        intent="list_events",
        start=start,
        end=end,
        search_text=None,
        original_text=normalized,
    )


def check_calendar_health(*, canonical_execution=None, canonical_authority: bool = False) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.health()
    if canonical_authority:
        return {
            "status": "disabled",
            "service": "oracle-brain",
            "calendar_configured": False,
            "timezone": "UTC",
            "detail": "Calendar feed is not configured",
        }
    settings = get_calendar_settings()
    if not settings["calendar_configured"]:
        return {
            "status": "failed",
            "service": "oracle-brain",
            "calendar_configured": False,
            "timezone": settings["timezone"],
            "detail": "Calendar feed is not configured",
        }
    try:
        event_count = len(
            _load_events_for_scope(
                settings,
                scope="personal",
                require_config=True,
                force_refresh=True,
                allow_stale=False,
            ).value
        )
        return {
            "status": "ok",
            "service": "oracle-brain",
            "calendar_configured": True,
            "timezone": settings["timezone"],
            "detail": f"Calendar feed reachable with {event_count} events parsed",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "service": "oracle-brain",
            "calendar_configured": True,
            "timezone": settings["timezone"],
            "detail": str(exc),
        }


def execute_calendar_query(query: CalendarQuery) -> dict[str, Any]:
    settings = get_calendar_settings()
    if not settings["calendar_configured"]:
        raise HTTPException(status_code=500, detail="Calendar feed is not configured")

    cached = _load_events_for_scope(settings, scope="personal", require_config=True)
    events = cached.value

    if query.intent == "find_event":
        result = _find_matching_event(query, events, settings["timezone"])
    else:
        result = _list_events(query, events, settings["timezone"])
    return {
        **result,
        "freshness": cached.freshness,
        "age_seconds": round(cached.age_seconds, 3),
        "stale_reason": cached.stale_reason,
        "stale_notice": (
            "I couldn't refresh the calendar, so these are the latest saved events."
            if cached.freshness == "stale"
            else None
        ),
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


def _find_matching_event(query: CalendarQuery, events: list[CalendarEvent], timezone_name: str) -> dict[str, Any]:
    target = _normalize_search_text(query.search_text or "")
    target_tokens = _search_tokens(target)
    matched = []
    now = _now_in_calendar_timezone(timezone_name)
    for event in events:
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
    return {
        "action": "find_event",
        "query": query.search_text,
        "events": [_event_to_payload(top, timezone_name)],
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


def _load_events_for_scope(
    settings: dict[str, Any],
    *,
    scope: str,
    require_config: bool,
    force_refresh: bool = False,
    allow_stale: bool = True,
) -> CachedRead[list[CalendarEvent]]:
    bridge = get_calendar_bridge(settings)
    url = settings.get("ics_url") if scope == "personal" else settings.get("holiday_ics_url")

    def load() -> list[CalendarEvent]:
        try:
            return bridge.fetch_events(
                settings=settings,
                scope=scope,
                require_config=require_config,
            )
        except CalendarBridgeError as exc:
            raise RuntimeError(exc.detail) from exc

    return _CALENDAR_CACHE.read(
        f"calendar:{scope}:{url}:{settings.get('timezone')}",
        ttl_seconds=CALENDAR_TTL_SECONDS,
        stale_max_seconds=CALENDAR_STALE_MAX_SECONDS,
        loader=load,
        force_refresh=force_refresh,
        allow_stale=allow_stale,
    )


def _event_to_payload(event: CalendarEvent, timezone_name: str) -> dict[str, Any]:
    return {
        "uid": event.uid,
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": event.all_day,
        "location": event.location,
        "timezone": timezone_name,
    }


def _normalize_search_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", " ".join(value.strip().lower().split()))


def _search_tokens(value: str) -> list[str]:
    stopwords = {"a", "an", "the", "my", "on", "at", "for", "to", "of"}
    return [token for token in value.split(" ") if token and token not in stopwords]


def _now_in_calendar_timezone(timezone_name: str | None = None) -> datetime:
    if timezone_name is None:
        timezone_name = str(get_calendar_settings()["timezone"])
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

    if "afternoon" in text:
        start = max(start, datetime.combine(start.date(), time(12, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(17, 0), tzinfo=now.tzinfo))
    elif "morning" in text:
        start = max(start, datetime.combine(start.date(), time(6, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(12, 0), tzinfo=now.tzinfo))
    elif "evening" in text or "tonight" in text:
        start = max(start, datetime.combine(start.date(), time(17, 0), tzinfo=now.tzinfo))
        end = min(end, datetime.combine(start.date(), time(23, 59), tzinfo=now.tzinfo))

    return start, end


def _extract_weekday(text: str) -> int | None:
    for name, index in WEEKDAY_NAMES.items():
        if name in text:
            return index
    return None
