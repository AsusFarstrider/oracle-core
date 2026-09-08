from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astral import Depression, Observer
from astral.sun import dawn, dusk, sunrise, sunset

from .configuration.weather_runtime_settings import (
    SolarWeatherLocationSettings,
    SolarWeatherRuntimeSettings,
)


_SOLAR_EVENTS = {
    "sunrise": ("sunrise", "sun rise", "sun come up"),
    "sunset": ("sunset", "sun set", "sun go down"),
    "civil_dawn": ("civil dawn", "first light"),
    "civil_dusk": ("civil dusk", "last light"),
}


class SolarWeatherError(RuntimeError):
    error_code = "solar_unavailable"


class SolarLocationError(SolarWeatherError):
    error_code = "solar_location_unresolved"


class SolarNoEventError(SolarWeatherError):
    error_code = "solar_event_unavailable"


@dataclass(frozen=True)
class SolarWeatherQuery:
    event: str
    target_date: date
    location_id: str
    location_label: str
    context_used: bool = False


def detect_solar_weather_query(text: str) -> bool:
    normalized = _normalize(text)
    matched_phrase = next(
        (
            phrase
            for phrases in _SOLAR_EVENTS.values()
            for phrase in phrases
            if phrase in normalized
        ),
        None,
    )
    if matched_phrase is None:
        return False
    if normalized == matched_phrase or normalized.startswith(f"{matched_phrase} "):
        return True
    question_markers = (
        "when ",
        "what time",
        "which time",
        "time does",
        "does the sun",
        "will the sun",
    )
    date_markers = ("today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return any(marker in normalized for marker in (*question_markers, *date_markers))


def build_solar_response(
    text: str,
    *,
    settings: SolarWeatherRuntimeSettings,
    now: datetime | None = None,
) -> tuple[str, dict[str, object]]:
    if not settings.enabled:
        raise SolarWeatherError("Solar weather is not configured.")
    event = _event(text)
    if event is None:
        raise SolarWeatherError("I couldn't tell which solar time you wanted.")
    location = _location(text, settings)
    try:
        tzinfo = ZoneInfo(location.timezone)
    except ZoneInfoNotFoundError as exc:
        raise SolarWeatherError("That solar location has an invalid timezone.") from exc
    reference = now.astimezone(tzinfo) if now is not None else datetime.now(tzinfo)
    target_date = _target_date(text, reference)
    observer = Observer(latitude=location.latitude, longitude=location.longitude)
    calculator = {
        "sunrise": lambda: sunrise(observer, date=target_date, tzinfo=tzinfo),
        "sunset": lambda: sunset(observer, date=target_date, tzinfo=tzinfo),
        "civil_dawn": lambda: dawn(
            observer, date=target_date, depression=Depression.CIVIL, tzinfo=tzinfo
        ),
        "civil_dusk": lambda: dusk(
            observer, date=target_date, depression=Depression.CIVIL, tzinfo=tzinfo
        ),
    }[event]
    try:
        event_time = calculator()
    except ValueError as exc:
        label = event.replace("_", " ")
        raise SolarNoEventError(
            f"There is no {label} at {location.label} on {target_date.isoformat()}."
        ) from exc
    day_label = _day_label(target_date, reference.date())
    spoken_event = event.replace("_", " ")
    speech = (
        f"{spoken_event.capitalize()} {day_label} at {location.label} is at "
        f"{event_time.strftime('%-I:%M %p')}."
    )
    return speech, {
        "event": event,
        "event_time": event_time.isoformat(),
        "date": target_date.isoformat(),
        "location_id": location.id,
        "location": location.label,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone,
        "source_type": "astral_local_calculation",
        "evidence": {
            "observer": {"latitude": location.latitude, "longitude": location.longitude},
            "civil_depression_degrees": 6,
        },
    }


def build_solar_day_snapshot(
    *,
    settings: SolarWeatherRuntimeSettings,
    target_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    location = _default_location(settings)
    if location is None:
        raise SolarLocationError("No default solar location is configured.")
    reference = now or datetime.now(ZoneInfo(location.timezone))
    day = target_date or reference.astimezone(ZoneInfo(location.timezone)).date()
    events: dict[str, str | None] = {}
    unavailable: list[str] = []
    for event in _SOLAR_EVENTS:
        try:
            _speech, payload = build_solar_response(
                f"{event.replace('_', ' ')} on {day.isoformat()}", settings=settings, now=reference
            )
            events[event] = str(payload["event_time"])
        except SolarNoEventError:
            events[event] = None
            unavailable.append(event)
    return {
        "date": day.isoformat(),
        "location_id": location.id,
        "location": location.label,
        "timezone": location.timezone,
        "source_type": "astral_local_calculation",
        "events": events,
        "unavailable_events": unavailable,
    }


def _event(text: str) -> str | None:
    normalized = _normalize(text)
    for event, phrases in _SOLAR_EVENTS.items():
        if any(phrase in normalized for phrase in phrases):
            return event
    return None


def _location(text: str, settings: SolarWeatherRuntimeSettings) -> SolarWeatherLocationSettings:
    normalized = _normalize(text)
    matches: list[SolarWeatherLocationSettings] = []
    for location in settings.locations.values():
        terms = (location.id, location.label, *location.aliases)
        if any(re.search(rf"\b{re.escape(_normalize(term))}\b", normalized) for term in terms):
            matches.append(location)
    unique = {item.id: item for item in matches}
    if len(unique) > 1:
        raise SolarLocationError("That solar location is ambiguous. Please use one configured place name.")
    if unique:
        return next(iter(unique.values()))
    explicit = re.search(r"\b(?:in|at|for)\s+([a-z][a-z .'-]{1,60})$", normalized)
    if explicit and explicit.group(1) not in {"today", "tomorrow"}:
        raise SolarLocationError("I only know solar times for configured locations.")
    default = _default_location(settings)
    if default is None:
        raise SolarLocationError("Please name a configured solar location.")
    return default


def _default_location(settings: SolarWeatherRuntimeSettings) -> SolarWeatherLocationSettings | None:
    if settings.home_location_id:
        return settings.locations.get(settings.home_location_id)
    if len(settings.locations) == 1:
        return next(iter(settings.locations.values()))
    return None


def _target_date(text: str, reference: datetime) -> date:
    normalized = _normalize(text)
    if "tomorrow" in normalized:
        return reference.date() + timedelta(days=1)
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for index, weekday in enumerate(weekdays):
        if weekday in normalized:
            days_ahead = (index - reference.weekday()) % 7
            return reference.date() + timedelta(days=days_ahead)
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", normalized)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError as exc:
            raise SolarWeatherError("That solar date is invalid.") from exc
    return reference.date()


def _day_label(target: date, today: date) -> str:
    if target == today:
        return "today"
    if target == today + timedelta(days=1):
        return "tomorrow"
    return f"on {target.strftime('%A, %B %-d')}"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().strip(" .?!").split())
