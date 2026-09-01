from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .math_calculator import build_math_response
from .unit_converter import build_conversion_response, parse_conversion_query


@dataclass(frozen=True)
class DateCalculationQuery:
    kind: str
    target_text: str


MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def parse_date_calculation_query(text: str) -> DateCalculationQuery | None:
    normalized = " ".join(text.strip().lower().split()).rstrip(" ?.!")
    if not normalized:
        return None

    patterns = (
        (r"^(?:how many days|how long) until (?P<target>.+)$", "until"),
        (r"^how many days since (?P<target>.+)$", "since"),
        (r"^what day of the week is (?P<target>.+)$", "weekday"),
    )
    for pattern, kind in patterns:
        match = re.match(pattern, normalized)
        if match:
            target_text = str(match.group("target")).strip()
            if target_text:
                return DateCalculationQuery(kind=kind, target_text=target_text)
    return None


def _normalize_target_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"\b(?:the|a|an)\b", " ", normalized)
    normalized = normalized.replace("'", "")
    normalized = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", normalized)
    return " ".join(normalized.split())


def _extract_target_year(text: str, *, today: date) -> tuple[str, int | None]:
    normalized = _normalize_target_text(text)
    if normalized.endswith(" this year"):
        return normalized[: -len(" this year")].strip(), today.year
    if normalized.endswith(" next year"):
        return normalized[: -len(" next year")].strip(), today.year + 1
    match = re.search(r"\b(20\d{2}|19\d{2})\b$", normalized)
    if match:
        year = int(match.group(1))
        return normalized[: match.start()].strip(), year
    return normalized, None


def _parse_numeric_date(text: str) -> date | None:
    for pattern in (
        r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})$",
        r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$",
    ):
        match = re.match(pattern, text)
        if not match:
            continue
        year = int(match.groupdict().get("year") or 0)
        month = int(match.group("month"))
        day = int(match.group("day"))
        if year == 0:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _resolve_explicit_date(target_text: str, *, kind: str, today: date) -> tuple[date, str] | None:
    base_text, requested_year = _extract_target_year(target_text, today=today)
    short_numeric = re.match(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})$", base_text)
    if short_numeric:
        candidate = _apply_missing_year(
            int(short_numeric.group("month")),
            int(short_numeric.group("day")),
            requested_year,
            kind=kind,
            today=today,
        )
        if candidate is None:
            return None
        return candidate, _format_date_label(candidate)

    numeric_date = _parse_numeric_date(base_text)
    if numeric_date is not None:
        return numeric_date, _format_date_label(numeric_date)

    normalized = _normalize_target_text(base_text)
    match = re.match(r"^(?P<month>[a-z]+) (?P<day>\d{1,2})$", normalized)
    if not match:
        return None
    month = MONTH_NAMES.get(match.group("month"))
    if month is None:
        return None
    candidate = _apply_missing_year(month, int(match.group("day")), requested_year, kind=kind, today=today)
    if candidate is None:
        return None
    return candidate, _format_date_label(candidate)


def _apply_missing_year(
    month: int,
    day_of_month: int,
    requested_year: int | None,
    *,
    kind: str,
    today: date,
) -> date | None:
    candidate_year = requested_year if requested_year is not None else today.year
    try:
        candidate = date(candidate_year, month, day_of_month)
    except ValueError:
        return None
    if requested_year is not None:
        return candidate
    if kind in {"until", "weekday"} and candidate < today:
        try:
            return date(today.year + 1, month, day_of_month)
        except ValueError:
            return None
    if kind == "since" and candidate > today:
        try:
            return date(today.year - 1, month, day_of_month)
        except ValueError:
            return None
    return candidate


def _format_date_label(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _normalize_event_summary(summary: str) -> str:
    normalized = summary.strip().lower().replace("'", "")
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"\bday\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9 ]", " ", normalized)
    return " ".join(normalized.split())


def _resolve_holiday_date(
    target_text: str,
    *,
    kind: str,
    today: date,
    calendar_execution=None,
) -> tuple[date, str] | None:
    base_text, requested_year = _extract_target_year(target_text, today=today)
    normalized_target = _normalize_event_summary(base_text)
    if not normalized_target:
        return None

    if calendar_execution is None:
        return None
    events = calendar_execution.load_events(scope="holiday").value
    candidates: list[tuple[int, date, str]] = []
    target_tokens = tuple(token for token in normalized_target.split() if token)
    for event in events:
        event_date = event.start.date()
        if requested_year is not None and event_date.year != requested_year:
            continue
        normalized_summary = _normalize_event_summary(event.summary)
        if normalized_summary == normalized_target:
            score = 100
        elif normalized_target in normalized_summary:
            score = 80
        elif target_tokens and all(token in normalized_summary for token in target_tokens):
            score = 60
        else:
            continue
        candidates.append((score, event_date, event.summary))

    if not candidates:
        return None

    if kind in {"until", "weekday"}:
        future = [item for item in candidates if item[1] >= today]
        pool = future or candidates
        _, resolved_date, summary = sorted(pool, key=lambda item: (-item[0], item[1]))[0]
        return resolved_date, summary

    past = [item for item in candidates if item[1] <= today]
    pool = past or candidates
    _, resolved_date, summary = sorted(pool, key=lambda item: (item[1], item[0]), reverse=True)[0]
    return resolved_date, summary


def _resolve_date_target(
    target_text: str,
    *,
    kind: str,
    today: date,
    calendar_execution=None,
) -> tuple[date, str]:
    explicit = _resolve_explicit_date(target_text, kind=kind, today=today)
    if explicit is not None:
        return explicit
    holiday = _resolve_holiday_date(
        target_text,
        kind=kind,
        today=today,
        calendar_execution=calendar_execution,
    )
    if holiday is not None:
        return holiday
    raise ValueError("I couldn't resolve that date.")


def _format_delta_days(delta_days: int) -> str:
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "1 day"
    return f"{delta_days} days"


def _build_date_calculation_response(
    query: DateCalculationQuery,
    *,
    today: date,
    calendar_execution=None,
) -> tuple[str, dict]:
    target_date, label = _resolve_date_target(
        query.target_text,
        kind=query.kind,
        today=today,
        calendar_execution=calendar_execution,
    )

    if query.kind == "weekday":
        speech = f"{label} is on a {target_date.strftime('%A')}."
        return speech, {
            "kind": "date_weekday",
            "target": label,
            "date": target_date.isoformat(),
            "weekday": target_date.strftime("%A"),
        }

    delta_days = (target_date - today).days
    if query.kind == "until":
        if delta_days == 0:
            speech = f"{label} is today."
        else:
            speech = f"There are {_format_delta_days(delta_days)} until {label}."
        return speech, {
            "kind": "date_until",
            "target": label,
            "date": target_date.isoformat(),
            "days": delta_days,
        }

    elapsed_days = abs(delta_days)
    if elapsed_days == 0:
        speech = f"{label} is today."
    else:
        speech = f"It has been {_format_delta_days(elapsed_days)} since {label}."
    return speech, {
        "kind": "date_since",
        "target": label,
        "date": target_date.isoformat(),
        "days": elapsed_days,
    }


def build_calculation_response(
    text: str,
    *,
    today: date | None = None,
    calendar_execution=None,
    math_context: dict[str, object] | None = None,
    conversion_context: dict[str, object] | None = None,
) -> tuple[str, dict]:
    date_query = parse_date_calculation_query(text)
    if date_query is not None:
        resolved_today = today or datetime.now().astimezone().date()
        return _build_date_calculation_response(
            date_query,
            today=resolved_today,
            calendar_execution=calendar_execution,
        )
    conversion = parse_conversion_query(text, conversion_context=conversion_context)
    if conversion is not None:
        return build_conversion_response(text, conversion_context=conversion_context)
    return build_math_response(text, math_context=math_context)
