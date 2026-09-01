from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from .deterministic_values import parse_duration, parse_number, parse_ordinal


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# This is deliberately a bounded product vocabulary, not a geocoder. Explicit
# installed IANA identifiers are also accepted case-insensitively.
TIMEZONE_ALIASES: dict[str, str] = {
    "utc": "UTC", "gmt": "UTC",
    "eastern": "America/New_York", "eastern time": "America/New_York",
    "new york": "America/New_York", "new york city": "America/New_York",
    "central": "America/Chicago", "central time": "America/Chicago", "chicago": "America/Chicago",
    "mountain": "America/Denver", "mountain time": "America/Denver", "denver": "America/Denver",
    "pacific": "America/Los_Angeles", "pacific time": "America/Los_Angeles",
    "california": "America/Los_Angeles", "los angeles": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "arizona": "America/Phoenix", "phoenix": "America/Phoenix",
    "alaska": "America/Anchorage", "hawaii": "Pacific/Honolulu", "honolulu": "Pacific/Honolulu",
    "london": "Europe/London", "united kingdom": "Europe/London", "uk": "Europe/London",
    "paris": "Europe/Paris", "france": "Europe/Paris",
    "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "beijing": "Asia/Shanghai", "china": "Asia/Shanghai",
    "india": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
}
AMBIGUOUS_TIMEZONE_ALIASES: dict[str, tuple[str, ...]] = {
    "washington": ("Washington DC", "Washington state"),
    "georgia": ("Georgia the country", "Georgia the US state"),
}
_AMBIGUOUS_OPTION_ZONES = {
    "washington dc": "America/New_York", "washington state": "America/Los_Angeles",
    "georgia the country": "Asia/Tbilisi", "georgia the us state": "America/New_York",
}
_IANA_CASEFOLD = {item.casefold(): item for item in available_timezones()}


@dataclass(frozen=True)
class ZoneResolution:
    status: str
    requested: str
    timezone: str | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemporalQuery:
    kind: str
    text: str
    subject: str = ""
    destination: str = ""
    source: str = ""
    amount_seconds: int = 0
    clock_text: str = ""
    relative: str = ""


def resolve_timezone(value: str) -> ZoneResolution:
    requested = " ".join(str(value or "").strip().lower().split())
    if requested in {"here", "local", "home"}:
        return ZoneResolution("household", requested)
    ambiguous = AMBIGUOUS_TIMEZONE_ALIASES.get(requested)
    if ambiguous:
        return ZoneResolution("ambiguous", requested, options=ambiguous)
    timezone_name = TIMEZONE_ALIASES.get(requested) or _AMBIGUOUS_OPTION_ZONES.get(requested) or _IANA_CASEFOLD.get(requested)
    if timezone_name:
        return ZoneResolution("resolved", requested, timezone=timezone_name)
    return ZoneResolution("unsupported", requested)


def resolve_local_wall_time(local_date: date, local_time: time, timezone_name: str) -> datetime:
    """Resolve wall-clock intent: first instant after gaps, earlier side of folds."""

    zone = ZoneInfo(timezone_name)
    naive = datetime.combine(local_date, local_time)
    candidate = naive.replace(tzinfo=zone, fold=0)
    round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip == naive:
        return candidate
    # A gap round-trips forward. Searching also makes the policy explicit on
    # zone implementations whose normalization differs.
    for minutes in range(1, 181):
        shifted = naive + timedelta(minutes=minutes)
        possible = shifted.replace(tzinfo=zone, fold=0)
        if possible.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == shifted:
            return possible
    raise ValueError(f"Could not resolve local wall time in {timezone_name}")


def _duration_seconds(value: str) -> int | None:
    parsed = parse_duration(value, max_seconds=366 * 24 * 3600)
    return None if parsed is None else parsed.seconds


def parse_temporal_query(text: str) -> TemporalQuery | None:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return None
    if normalized.startswith("and "):
        nested = parse_temporal_query(normalized[4:])
        if nested is not None:
            return TemporalQuery(
                kind=nested.kind,
                text=normalized,
                subject=nested.subject,
                destination=nested.destination,
                source=nested.source,
                amount_seconds=nested.amount_seconds,
                clock_text=nested.clock_text,
                relative=nested.relative,
            )

    if re.fullmatch(r"(?:what(?:'s| is) )?(?:the )?time and date(?: is it)?|what time and date is it", normalized):
        return TemporalQuery("current_time_date", normalized)
    if re.fullmatch(r"(?:what(?:'s| is) )?(?:the )?(?:current )?time(?: is it)?|what time is it|tell me the time|time right now|the time", normalized):
        return TemporalQuery("current_time", normalized)
    if re.fullmatch(r"(?:what(?:'s| is) )?(?:the )?(?:current )?date(?: is it)?|what(?:'s| is) today(?:'s)? date|what day(?: of the week)? is it|tell me the date|today'?s date", normalized):
        return TemporalQuery("current_date", normalized)

    match = re.fullmatch(r"(?:what time is it|what(?:'s| is) the time|time) (?:in|at) (.+)", normalized)
    if match:
        return TemporalQuery("world_time", normalized, destination=match.group(1))
    match = re.fullmatch(r"what about (.+)", normalized)
    if match and resolve_timezone(match.group(1)).status != "unsupported":
        return TemporalQuery("temporal_followup", normalized, destination=match.group(1))
    match = re.fullmatch(r"(?:(?:what is|what's) the )?time difference between (.+?) and (.+)", normalized)
    if match:
        return TemporalQuery("world_difference", normalized, source=match.group(1), destination=match.group(2))
    match = re.fullmatch(r"how many hours (?:ahead|behind) is (.+?) (?:from|than) (.+)", normalized)
    if match:
        return TemporalQuery("world_difference", normalized, source=match.group(2), destination=match.group(1))
    match = re.fullmatch(r"if it(?:'s| is) (.+?) (?:here|at home) what time (?:is it|would it be) in (.+)", normalized)
    if match:
        return TemporalQuery("world_convert", normalized, source="here", destination=match.group(2), clock_text=match.group(1))

    match = re.fullmatch(r"how (?:much )?(?:long|many (?:minutes|hours)) (?:is it )?(?:until|till) (.+)", normalized)
    if match:
        target_text = match.group(1)
        if _parse_clock(target_text) is not None or target_text.endswith(" tomorrow morning"):
            return TemporalQuery("time_until", normalized, clock_text=target_text)
        return TemporalQuery("date_until", normalized, subject=target_text)
    match = re.fullmatch(r"how (?:much )?(?:long|many (?:minutes|hours)) (?:has it been )?since (.+)", normalized)
    if match:
        return TemporalQuery("time_since", normalized, clock_text=match.group(1))
    match = re.fullmatch(r"what time will it be in (.+)", normalized)
    if match and (seconds := _duration_seconds(match.group(1))) is not None:
        return TemporalQuery("time_shift", normalized, amount_seconds=seconds, relative="future")
    match = re.fullmatch(r"(?:what time will it be )?(.+?) (?:from now|in the future)", normalized)
    if match and (seconds := _duration_seconds(match.group(1))) is not None:
        return TemporalQuery("time_shift", normalized, amount_seconds=seconds, relative="future")
    match = re.fullmatch(r"(?:what time (?:was it )?|what was the time )?(.+?) ago", normalized)
    if match and (seconds := _duration_seconds(match.group(1))) is not None:
        return TemporalQuery("time_shift", normalized, amount_seconds=seconds, relative="past")

    match = re.fullmatch(r"(?:what(?:'s| is) the |what )(?:date|day) (?:is |will it be )?(.+?) from (?:today|now)", normalized)
    if match and (seconds := _duration_seconds(match.group(1))) is not None:
        return TemporalQuery("date_shift", normalized, amount_seconds=seconds, relative="future")
    match = re.fullmatch(r"what (?:date|day) was (?:it )?(.+?) ago", normalized)
    if match and (seconds := _duration_seconds(match.group(1))) is not None:
        return TemporalQuery("date_shift", normalized, amount_seconds=seconds, relative="past")
    match = re.fullmatch(r"how many days (until|till|since) (.+)", normalized)
    if match:
        return TemporalQuery("date_since" if match.group(1) == "since" else "date_until", normalized, subject=match.group(2))
    match = re.fullmatch(r"how many days (?:are(?: there)? )?between (.+?) and (.+)", normalized)
    if match:
        return TemporalQuery("date_between", normalized, source=match.group(1), destination=match.group(2))
    match = re.fullmatch(r"what day(?: of the week)? (?:is|was|will) (.+)", normalized)
    if match:
        subject = match.group(1)
        if re.search(r"\b(?:my|appointment|meeting|event|calendar|sunrise|sunset)\b", subject):
            return None
        return TemporalQuery("date_weekday", normalized, subject=subject)
    match = re.fullmatch(r"what(?:'s| is) the date (.+)", normalized)
    if match:
        return TemporalQuery("date_when", normalized, subject=match.group(1))
    match = re.fullmatch(r"when is (?!my\b|the next\b)(.+)", normalized)
    if match:
        subject = match.group(1)
        if re.search(r"\b(?:appointment|meeting|event|calendar|sunrise|sunset)\b", subject):
            return None
        return TemporalQuery("date_when", normalized, subject=subject)
    return None


def _parse_clock(value: str) -> time | None:
    text = value.strip().lower().replace("o'clock", "").strip()
    if text == "noon":
        return time(12, 0)
    if text == "midnight":
        return time(0, 0)
    match = re.fullmatch(r"(.+?)(?::([0-5]?\d))?\s*(am|pm)?", text)
    if not match:
        return None
    hour_value = parse_number(match.group(1).strip())
    if hour_value is None or hour_value != hour_value.to_integral_value():
        return None
    hour, minute, meridiem = int(hour_value), int(match.group(2) or 0), match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif not 0 <= hour <= 23:
        return None
    return time(hour, minute)


def _clock_candidates(value: str) -> tuple[time, ...]:
    parsed = _parse_clock(value)
    if parsed is None:
        return ()
    text = value.strip().lower()
    if re.search(r"\b(?:am|pm)\b|noon|midnight", text) or parsed.hour == 0 or parsed.hour > 12:
        return (parsed,)
    return (time(parsed.hour % 12, parsed.minute), time(parsed.hour % 12 + 12, parsed.minute))


def parse_clock_candidates(value: str) -> tuple[time, ...]:
    """Return the bounded clock interpretations used by deterministic utilities."""

    return _clock_candidates(value)


def _relative_date(value: str, today: date, *, preference: str = "future") -> date | None:
    text = value.strip().lower()
    if text in {"today", "today's date"}:
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    weekday_match = re.fullmatch(r"(this|next|last) (" + "|".join(_WEEKDAYS) + r")", text)
    if weekday_match:
        relation, weekday = weekday_match.groups()
        target = _WEEKDAYS[weekday]
        if relation == "this":
            days = (target - today.weekday()) % 7
        elif relation == "next":
            days = (target - today.weekday()) % 7 + 7
        else:
            days = -((today.weekday() - target) % 7 or 7)
        return today + timedelta(days=days)
    if text == "this weekend":
        return today + timedelta(days=(_WEEKDAYS["saturday"] - today.weekday()) % 7)
    in_duration = re.fullmatch(r"in (.+)", text)
    if in_duration and (seconds := _duration_seconds(in_duration.group(1))) is not None:
        return today + timedelta(days=seconds // 86400)
    duration_match = re.fullmatch(r"(.+?) (from now|from today|ago)", text)
    if duration_match and (seconds := _duration_seconds(duration_match.group(1))) is not None:
        days = seconds // 86400
        return today + timedelta(days=-days if duration_match.group(2) == "ago" else days)
    month_match = re.fullmatch(r"(" + "|".join(_MONTHS) + r")\s+(.+?)(?:\s+(\d{4}|this year|next year|last year))?", text)
    if month_match:
        month = _MONTHS[month_match.group(1)]
        day_text = month_match.group(2)
        day_number = parse_ordinal(day_text)
        if day_number is None:
            cardinal = parse_number(day_text)
            day_number = int(cardinal) if cardinal is not None and cardinal == cardinal.to_integral_value() else None
        if day_number is None:
            return None
        year_text = month_match.group(3)
        year = today.year
        if year_text == "next year": year += 1
        elif year_text == "last year": year -= 1
        elif year_text and year_text.isdigit(): year = int(year_text)
        try:
            result = date(year, month, day_number)
        except ValueError:
            return None
        if year_text is None:
            if preference == "future" and result < today:
                result = result.replace(year=year + 1)
            elif preference == "past" and result > today:
                result = result.replace(year=year - 1)
        return result
    numeric_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if numeric_match:
        month, day_number = int(numeric_match.group(1)), int(numeric_match.group(2))
        year = int(numeric_match.group(3) or today.year)
        if year < 100: year += 2000
        try:
            return date(year, month, day_number)
        except ValueError:
            return None
    return None


def parse_relative_date(value: str, today: date) -> date | None:
    """Resolve the public deterministic relative/date vocabulary for schedules."""

    return _relative_date(value, today, preference="future")


def _format_date(value: date) -> str:
    return value.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _format_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _format_duration(seconds: int) -> str:
    seconds = abs(int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    for amount, label in ((days, "day"), (hours, "hour"), (minutes, "minute"), (secs, "second")):
        if amount:
            parts.append(f"{amount} {label}{'' if amount == 1 else 's'}")
    return " and ".join(parts[:2]) or "0 seconds"


def _holiday_events(calendar_execution: object) -> list[object]:
    if calendar_execution is None:
        raise ValueError("The configured holiday calendar is unavailable.")
    result = calendar_execution.load_events(scope="holiday", require_config=True)
    return list(result.value)


def _holiday_date(subject: str, *, today: date, calendar_execution: object, preference: str = "future") -> tuple[date, str] | None:
    query = re.sub(r"\b(?:this|next|last) year\b", "", subject.lower()).strip()
    events = _holiday_events(calendar_execution)
    candidates: list[tuple[date, str]] = []
    for event in events:
        summary = str(getattr(event, "summary", "")).strip()
        event_date = getattr(event, "start").date()
        normalized_summary = summary.lower()
        if query == normalized_summary or query in normalized_summary or normalized_summary in query:
            candidates.append((event_date, summary))
    if "this year" in subject.lower():
        current_year = [item for item in candidates if item[0].year == today.year]
        return min(current_year, default=None, key=lambda item: item[0])
    if preference == "past":
        past = [item for item in candidates if item[0] <= today]
        return max(past or candidates, default=None, key=lambda item: item[0])
    future = [item for item in candidates if item[0] >= today]
    return min(future or candidates, default=None, key=lambda item: item[0])


def _resolve_date_subject(subject: str, *, today: date, calendar_execution: object, preference: str = "future") -> tuple[date, str] | None:
    explicit = _relative_date(subject, today, preference=preference)
    if explicit is not None:
        return explicit, _format_date(explicit)
    if re.match(r"^(?:" + "|".join(_MONTHS) + r")\b|^\d{1,2}/", subject.strip().lower()):
        raise ValueError("That is not a valid calendar date.")
    return _holiday_date(subject, today=today, calendar_execution=calendar_execution, preference=preference)


def build_temporal_response(
    query_or_text: TemporalQuery | str,
    *,
    household_timezone: str,
    calendar_execution: object | None = None,
    now: datetime | None = None,
    temporal_context: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    query = query_or_text if isinstance(query_or_text, TemporalQuery) else parse_temporal_query(query_or_text)
    if query is None:
        raise ValueError("Unsupported time or date request.")
    try:
        household_zone = ZoneInfo(household_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("The configured household timezone is invalid.") from exc
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    local_now = reference.astimezone(household_zone)
    kind = query.kind

    if kind == "current_time":
        speech = f"It is {_format_time(local_now)}."
        return speech, {"kind": kind, "timestamp": local_now.isoformat(), "timezone": household_timezone, "display_text": speech}
    if kind == "current_date":
        speech = f"Today is {_format_date(local_now.date())}."
        return speech, {"kind": kind, "date": local_now.date().isoformat(), "timezone": household_timezone, "display_text": speech}
    if kind == "current_time_date":
        speech = f"It is {_format_time(local_now)} on {_format_date(local_now.date())}."
        return speech, {"kind": kind, "timestamp": local_now.isoformat(), "timezone": household_timezone, "display_text": speech}

    if kind == "temporal_followup":
        context_payload = (temporal_context or {}).get("payload") if isinstance(temporal_context, dict) else None
        if not isinstance(context_payload, dict) or context_payload.get("subject_type") not in {"current_time", "world_time"}:
            raise ValueError("That time follow-up has no current temporal context.")
        kind = "world_time"

    if kind in {"world_time", "world_difference", "world_convert"}:
        destination = resolve_timezone(query.destination)
        if destination.status == "ambiguous":
            return "Which place did you mean: " + " or ".join(destination.options) + "?", {
                "kind": kind, "status": "clarification_required", "requested_location": destination.requested,
                "options": list(destination.options), "display_text": query.text,
            }
        if destination.status == "unsupported":
            return f"I don't support {query.destination} as a time location yet.", {"kind": kind, "status": "unsupported_location", "requested_location": destination.requested}
        destination_name = household_timezone if destination.status == "household" else str(destination.timezone)
        destination_zone = ZoneInfo(destination_name)
        if kind == "world_time":
            there = reference.astimezone(destination_zone)
            speech = f"In {query.destination.title()}, it is {_format_time(there)}."
            return speech, {"kind": kind, "timestamp": there.isoformat(), "timezone": destination_name, "location": query.destination, "display_text": speech}
        source = resolve_timezone(query.source)
        if source.status == "ambiguous":
            return "Which place did you mean: " + " or ".join(source.options) + "?", {"kind": kind, "status": "clarification_required", "requested_location": source.requested, "options": list(source.options), "display_text": query.text}
        if source.status == "unsupported":
            return f"I don't support {query.source} as a time location yet.", {"kind": kind, "status": "unsupported_location", "requested_location": source.requested}
        source_name = household_timezone if source.status == "household" else str(source.timezone)
        source_zone = ZoneInfo(source_name)
        if kind == "world_difference":
            source_offset = reference.astimezone(source_zone).utcoffset() or timedelta()
            destination_offset = reference.astimezone(destination_zone).utcoffset() or timedelta()
            difference = int((destination_offset - source_offset).total_seconds())
            if difference == 0:
                speech = f"{query.destination.title()} and {query.source.title()} have the same time."
                return speech, {"kind": kind, "difference_seconds": 0, "source_timezone": source_name, "timezone": destination_name, "display_text": speech}
            direction = "ahead of" if difference >= 0 else "behind"
            speech = f"{query.destination.title()} is {_format_duration(difference)} {direction} {query.source.title()}."
            return speech, {"kind": kind, "difference_seconds": difference, "source_timezone": source_name, "timezone": destination_name, "display_text": speech}
        clock = _parse_clock(query.clock_text)
        if clock is None:
            raise ValueError("I couldn't understand that clock time.")
        source_instant = resolve_local_wall_time(local_now.date(), clock, source_name)
        converted = source_instant.astimezone(destination_zone)
        speech = f"That would be {_format_time(converted)} in {query.destination.title()}."
        return speech, {"kind": kind, "timestamp": converted.isoformat(), "source_timezone": source_name, "timezone": destination_name, "display_text": speech}

    if kind == "time_shift":
        shifted = local_now + timedelta(seconds=query.amount_seconds * (-1 if query.relative == "past" else 1))
        speech = f"It {'was' if query.relative == 'past' else 'will be'} {_format_time(shifted)}."
        return speech, {"kind": kind, "timestamp": shifted.isoformat(), "timezone": household_timezone, "display_text": speech}
    if kind in {"time_until", "time_since"}:
        clock_text = query.clock_text
        day_offset = 0
        if clock_text.endswith(" tomorrow morning"):
            clock_text = clock_text.removesuffix(" tomorrow morning") + " am"; day_offset = 1
        clocks = _clock_candidates(clock_text)
        if not clocks:
            raise ValueError("I couldn't understand that clock time.")
        targets: list[datetime] = []
        for clock in clocks:
            target = resolve_local_wall_time(local_now.date() + timedelta(days=day_offset), clock, household_timezone)
            if kind == "time_until" and target <= local_now:
                target = resolve_local_wall_time(local_now.date() + timedelta(days=1), clock, household_timezone)
            if kind == "time_since" and target > local_now:
                target = resolve_local_wall_time(local_now.date() - timedelta(days=1), clock, household_timezone)
            targets.append(target)
        if kind == "time_until":
            target = min(targets, key=lambda item: item.astimezone(UTC))
        else:
            target = max(targets, key=lambda item: item.astimezone(UTC))
        seconds = int((target.astimezone(UTC) - local_now.astimezone(UTC)).total_seconds())
        speech = f"It is {_format_duration(seconds)} {'until' if kind == 'time_until' else 'since'} {_format_time(target)}."
        return speech, {"kind": kind, "seconds": abs(seconds), "timestamp": target.isoformat(), "timezone": household_timezone, "display_text": speech}
    if kind == "date_shift":
        days = query.amount_seconds // 86400
        target = local_now.date() + timedelta(days=days * (-1 if query.relative == "past" else 1))
        speech = f"That date is {_format_date(target)}."
        return speech, {"kind": kind, "date": target.isoformat(), "timezone": household_timezone, "display_text": speech}

    if kind == "date_between":
        first = _resolve_date_subject(query.source, today=local_now.date(), calendar_execution=calendar_execution)
        second = _resolve_date_subject(query.destination, today=local_now.date(), calendar_execution=calendar_execution)
        if first is None or second is None:
            raise ValueError("I couldn't resolve one of those dates from the configured calendar.")
        days = abs((second[0] - first[0]).days)
        speech = f"There {'is' if days == 1 else 'are'} {days} day{'' if days == 1 else 's'} between those dates."
        return speech, {"kind": kind, "days": days, "start_date": first[0].isoformat(), "end_date": second[0].isoformat(), "timezone": household_timezone, "display_text": speech}

    preference = "past" if kind == "date_since" else "future"
    resolved = _resolve_date_subject(query.subject, today=local_now.date(), calendar_execution=calendar_execution, preference=preference)
    if resolved is None:
        raise ValueError("That date is not present in the configured holiday calendar.")
    target, label = resolved
    if kind in {"date_until", "date_since"}:
        days = (target - local_now.date()).days if kind == "date_until" else (local_now.date() - target).days
        if days < 0:
            raise ValueError("That date is on the other side of today.")
        speech = f"There {'is' if days == 1 else 'are'} {days} day{'' if days == 1 else 's'} {'until' if kind == 'date_until' else 'since'} {label}."
    elif kind == "date_weekday":
        speech = f"{label} is on a {target.strftime('%A')}."
    else:
        speech = f"{label} is {_format_date(target)}."
    return speech, {"kind": kind, "date": target.isoformat(), "timezone": household_timezone, "display_text": speech}
