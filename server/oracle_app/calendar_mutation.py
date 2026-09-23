from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .calendar_models import CalendarEvent
from .calendar_write import _extract_date_value, _extract_start_time, _extract_end_or_duration


@dataclass(frozen=True)
class CalendarMutationPreparation:
    status: str
    prompt: str
    mutation: dict[str, object] | None = None
    options: tuple[str, ...] = ()


def prepare_calendar_mutation(
    text: str, *, events: list[CalendarEvent], timezone_name: str,
    write_feed_id: str | None, now: datetime | None = None,
) -> CalendarMutationPreparation:
    normalized = " ".join(str(text or "").casefold().split())
    operation = "delete" if re.search(r"\b(?:delete|remove|cancel)\b", normalized) else "edit"
    scope = "occurrence" if re.search(r"\b(?:this|one|single) occurrence\b", normalized) else "series" if re.search(r"\b(?:whole|entire) series\b", normalized) else None
    selector = _selector(normalized, operation)
    if not selector:
        return CalendarMutationPreparation("clarification", "Which Calendar event should I change?")
    candidates = [event for event in events if event.source_id == write_feed_id and selector in _normalize(event.summary)]
    target_date, _ = _extract_date_value(normalized, now=now or datetime.now(ZoneInfo(timezone_name)))
    if target_date:
        dated = [event for event in candidates if event.start.astimezone(ZoneInfo(timezone_name)).date().isoformat() == target_date]
        if dated:
            candidates = dated
    if not candidates:
        return CalendarMutationPreparation("not_found", f"I couldn't find a matching event in the writable calendar.")
    if len(candidates) != 1:
        options = tuple(f"{item.summary} — {item.start.astimezone(ZoneInfo(timezone_name)).strftime('%A, %B %-d at %-I:%M %p')}" for item in sorted(candidates, key=lambda event: event.start)[:5])
        return CalendarMutationPreparation("clarification", "I found more than one matching event. Which one do you mean?", options=options)
    event = candidates[0]
    if event.recurring and scope is None:
        return CalendarMutationPreparation("clarification", "Should that apply to this occurrence or the whole series?", options=("this occurrence", "the whole series"))
    scope = scope or "series"
    changes: dict[str, object] = {}
    if operation == "edit":
        rename = re.search(r"\b(?:rename|change)\s+.+?\s+(?:title\s+)?to\s+(.+?)(?:\s+(?:this occurrence|the whole series))?$", normalized)
        if rename and not re.search(r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", rename.group(1)):
            changes["title"] = rename.group(1).strip(" .")
        date_value, _ = _extract_date_value(normalized, now=now or datetime.now(ZoneInfo(timezone_name)))
        start_time, _, ambiguous = _extract_start_time(normalized)
        if ambiguous:
            return CalendarMutationPreparation("clarification", "What exact time should the event start?")
        if date_value or start_time or "all day" in normalized:
            zone = ZoneInfo(timezone_name)
            local_start = event.start.astimezone(zone)
            target_date_value = local_start.date() if not date_value else datetime.fromisoformat(date_value).date()
            target_all_day = True if "all day" in normalized else False if start_time else event.all_day
            if target_all_day:
                new_start = datetime.combine(target_date_value, datetime.min.time(), tzinfo=zone)
                new_end = new_start + timedelta(days=1)
            else:
                hour, minute = (local_start.hour, local_start.minute)
                if start_time:
                    hour, minute = (int(part) for part in start_time.split(":"))
                new_start = datetime.combine(target_date_value, datetime.min.time(), tzinfo=zone).replace(hour=hour, minute=minute)
                end_time, duration_minutes, _ = _extract_end_or_duration(normalized, start_time=start_time)
                if end_time:
                    end_hour, end_minute = (int(part) for part in end_time.split(":"))
                    new_end = datetime.combine(target_date_value, datetime.min.time(), tzinfo=zone).replace(hour=end_hour, minute=end_minute)
                    if new_end <= new_start:
                        new_end += timedelta(days=1)
                elif duration_minutes:
                    new_end = new_start + timedelta(minutes=duration_minutes)
                else:
                    new_end = new_start + (event.end - event.start)
            changes.update(start=new_start, end=new_end, all_day=target_all_day)
        if not changes:
            return CalendarMutationPreparation("clarification", "What should I change about that event?")
    mutation = {
        "operation": operation, "uid": event.uid, "source_id": event.source_id,
        "recurrence_scope": scope,
        "recurrence_id": event.recurrence_id or (event.start.isoformat() if event.recurring and scope == "occurrence" else None),
        "changes": changes,
        "summary": event.summary, "original_start": event.start.isoformat(),
    }
    verb = "delete" if operation == "delete" else "change"
    return CalendarMutationPreparation("confirmation", f"Do you want me to {verb} {event.summary}?", mutation=mutation)


def _selector(text: str, operation: str) -> str:
    working = re.sub(r"^(?:please\s+)?(?:delete|remove|cancel|edit|update|reschedule|move|rename|change)\s+", "", text)
    working = re.split(r"\s+(?:to|on)\s+(?:today|tomorrow|next\s+\w+|\w+day|\d)", working, maxsplit=1)[0]
    working = re.split(r"\s+(?:this occurrence|the whole series|whole series|entire series)\b", working, maxsplit=1)[0]
    working = re.sub(r"\b(?:calendar\s+)?(?:event|appointment)\b", "", working)
    working = re.sub(r"\s+(?:from|on)\s+(?:my|the)\s+calendar\b.*$", "", working)
    working = re.sub(r"^(?:my|the)\s+", "", working)
    if operation == "edit":
        working = re.split(r"\s+(?:title\s+)?to\s+", working, maxsplit=1)[0]
    return _normalize(working)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", value.casefold()).split())
