from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class HelpCapability:
    key: str
    label: str
    aliases: tuple[str, ...]
    summary: str
    examples: tuple[str, ...]
    handler_target: str | None = None
    disposition: str = "supported"
    unavailable_text: str = ""


CAPABILITY_CATALOG: tuple[HelpCapability, ...] = (
    HelpCapability("time", "time and dates", ("time", "date", "dates", "world time", "time zones"), "tell local or world time, compare time zones, and do date arithmetic", ("what time is it in London", "what date is two weeks from today")),
    HelpCapability("timers", "timers", ("timer", "timers", "countdown", "countdowns"), "set, name, inspect, adjust, restart, cancel, and dismiss concurrent timers", ("set a pasta timer for 12 minutes", "add five minutes to the pasta timer")),
    HelpCapability("alarms", "alarms", ("alarm", "alarms", "weekday alarm", "recurring alarm"), "set and manage one-time or recurring alarms, including occurrence and series changes", ("set a weekday alarm for 7", "change tomorrow's alarm to 7:15")),
    HelpCapability("reminders", "reminders", ("reminder", "reminders", "recurring reminder"), "create and manage one-time or recurring personal, named-person, or everyone reminders", ("remind me at 6 to call Mom", "change my weekday reminder to 7")),
    HelpCapability("math", "math", ("math", "calculations", "calculation", "arithmetic", "percentages"), "perform bounded arithmetic, percentages, roots, powers, proportions, and explicit-input geometry", ("what is 15 percent of 80", "divide that by four")),
    HelpCapability("conversions", "unit conversions", ("conversion", "conversions", "unit conversion", "units"), "convert supported fixed units, including mixed units and temperatures", ("convert 10 kilometers to miles", "what about 12 miles")),
    HelpCapability("repeat", "Repeat", ("repeat", "repeat that", "say that again"), "repeat the last eligible Oracle reply in this interaction", ("repeat that",)),
    HelpCapability("home", "house controls", ("home", "house", "devices", "lights", "home assistant"), "control and inspect configured household devices", ("turn on the living room lights",), handler_target="home_assistant"),
    HelpCapability("calendar", "calendar", ("calendar", "calendars", "events"), "read the configured calendar and create, edit, or delete events", ("what's on my calendar tomorrow", "add lunch Friday at noon"), handler_target="calendar"),
    HelpCapability("music", "music", ("music", "songs", "albums", "playlists"), "search, play, and control configured music playback", ("play David Bowie", "pause the music"), handler_target="music"),
    HelpCapability("audiobooks", "audiobooks", ("audiobook", "audiobooks", "books"), "search, play, resume, and control configured audiobooks", ("play my audiobook",), handler_target="audiobook"),
    HelpCapability("weather", "weather", ("weather", "forecast", "forecasts"), "report configured current, forecast, remote, and bounded historical weather", ("what's the weather tomorrow",), handler_target="weather"),
    HelpCapability("news", "news", ("news", "headlines"), "read headlines from configured news sources", ("latest headlines",), handler_target="news"),
    HelpCapability("facts", "fact lookup", ("facts", "fact lookup", "definitions", "questions"), "look up bounded factual answers from configured evidence providers", ("what is photosynthesis",), handler_target="facts"),
    HelpCapability("network", "network status", ("network", "internet", "wifi", "wi fi"), "check and safely recover the configured household network", ("how's the network",), handler_target="network"),
    HelpCapability("stopwatch", "stopwatches", ("stopwatch", "stopwatches", "lap timer"), "", (), disposition="deferred", unavailable_text="Stopwatches are not supported yet; they are deferred until after V2."),
    HelpCapability("randomizer", "random choices", ("random", "randomizer", "coin", "coin flip", "dice", "die", "random number"), "", (), disposition="deferred", unavailable_text="Coin flips, dice, and random choices are not supported yet; they are deferred until after V2."),
    HelpCapability("sun", "sunrise and sunset", ("sunrise", "sunset", "sunrise and sunset"), "", (), disposition="deferred", unavailable_text="Sunrise and sunset are not supported yet; they are assigned to the later Weather stage."),
    HelpCapability("lists", "lists and notes", ("list", "lists", "shopping list", "grocery list", "note", "notes"), "", (), disposition="deferred", unavailable_text="Lists and notes are not supported yet; they are assigned to Stage 8."),
    HelpCapability("messaging", "calls and messages", ("call", "calls", "phone call", "phone calls", "message", "messages", "text message", "text messages", "announcement", "intercom"), "", (), disposition="deferred", unavailable_text="Calls, messages, and general announcements are not supported; household communications are deferred to V3."),
    HelpCapability("currency", "live currency conversion", ("currency", "exchange rate", "dollars to euros", "money conversion"), "", (), disposition="deferred", unavailable_text="Live currency conversion is not supported because Oracle has no current exchange-rate provider."),
)


_TASK_GUIDANCE = {
    "change_reminder": "Name the reminder and say what to change, for example, ‘change my medicine reminder to 8 PM.’ For a recurring reminder, say ‘tomorrow’ or ‘next occurrence’ to change only that occurrence, or name the recurring schedule to change the series.",
    "turn_off_weekday_alarm": "Say ‘cancel my weekday alarm’ and include its name or time if you have more than one.",
    "cancel_timer": "Say ‘cancel the pasta timer’ or identify it by duration. If only one timer is running, ‘cancel my timer’ is enough.",
    "change_tomorrow_alarm": "Say ‘change tomorrow's alarm to 7:15.’ That changes only tomorrow's occurrence; naming the weekday schedule changes the recurring series.",
}


def classify_help_request(text: str) -> tuple[str, str | None] | None:
    normalized = " ".join(str(text or "").casefold().split()).strip(" ?.!")
    if normalized in {"help", "what can you do", "what can i ask you", "what do you do"}:
        return "general", None
    if normalized in {"help with that", "why couldn't you do that", "what went wrong"}:
        return "failure", None

    task_patterns = (
        (r"how (?:do|can) i change (?:a|my) reminder", "change_reminder"),
        (r"how (?:do|can) i (?:turn off|cancel) (?:a|my) weekday alarm", "turn_off_weekday_alarm"),
        (r"how (?:do|can) i cancel (?:a|my) timer", "cancel_timer"),
        (r"how (?:do|can) i change only tomorrow(?:'s|s) alarm", "change_tomorrow_alarm"),
    )
    for pattern, task in task_patterns:
        if re.fullmatch(pattern, normalized):
            return "task", task

    subject = None
    unmatched_is_help = False
    for pattern, explicit_support_question in (
        (r"what can you do with (.+)", True),
        (r"how do (.+) work", True),
        (r"what kinds? of (.+) can i (?:set|use|ask for)", True),
        (r"can you (?:do|use|manage|handle) (.+)", True),
        (r"can you (?:set|create|flip|roll|start|make|tell me) (.+)", False),
        (r"do you support (.+)", True),
    ):
        matched = re.fullmatch(pattern, normalized)
        if matched:
            subject = matched.group(1).strip()
            unmatched_is_help = explicit_support_question
            break
    if subject is None:
        return None
    capability = find_capability(subject)
    if capability is not None:
        return "capability", capability.key
    return ("unsupported", subject) if unmatched_is_help else None


def find_capability(subject: str) -> HelpCapability | None:
    normalized = " ".join(str(subject or "").casefold().split()).strip(" ?.!")
    normalized = re.sub(r"^(?:a|an|the|my|some)\s+", "", normalized)
    normalized = re.sub(r"\s+(?:for me|yet)$", "", normalized)
    for capability in CAPABILITY_CATALOG:
        if normalized == capability.key or normalized in capability.aliases:
            return capability
    return None


def render_help(kind: str, subject: str | None, *, registry: Any) -> dict[str, str]:
    if kind == "task" and subject in _TASK_GUIDANCE:
        return {"help_kind": "task", "topic": subject, "speech": _TASK_GUIDANCE[subject]}
    if kind == "unsupported":
        return {
            "help_kind": "unsupported",
            "topic": str(subject or ""),
            "speech": f"I don't have a supported capability for {subject}. Try ‘help’ for a short list of what I can do.",
        }
    if kind == "capability":
        capability = next((item for item in CAPABILITY_CATALOG if item.key == subject), None)
        if capability is None:
            return render_help("unsupported", subject, registry=registry)
        if capability.disposition != "supported":
            return {"help_kind": "unavailable", "topic": capability.key, "speech": capability.unavailable_text}
        if not capability_enabled(capability, registry=registry):
            return {
                "help_kind": "disabled",
                "topic": capability.key,
                "speech": f"{capability.label.capitalize()} is supported by Oracle but is not enabled in this configuration.",
            }
        examples = "’ or ‘".join(capability.examples[:2])
        speech = f"I can {capability.summary}."
        if examples:
            speech += f" Try ‘{examples}.’"
        return {"help_kind": "capability", "topic": capability.key, "speech": speech}

    enabled = [item.label for item in CAPABILITY_CATALOG if item.disposition == "supported" and capability_enabled(item, registry=registry)]
    preferred = [name for name in ("timers", "alarms", "reminders", "time and dates", "math", "unit conversions", "house controls", "calendar", "music") if name in enabled]
    overview = preferred[:7]
    return {
        "help_kind": "general",
        "topic": "",
        "speech": "I can help with " + ", ".join(overview[:-1]) + (f", and {overview[-1]}" if len(overview) > 1 else overview[0]) + ". Ask ‘what can you do with alarms?’ for details.",
    }


def capability_enabled(capability: HelpCapability, *, registry: Any) -> bool:
    if capability.handler_target is None:
        return True
    handler = registry.get(capability.handler_target) if registry is not None else None
    if handler is None:
        return False
    if capability.handler_target == "home_assistant":
        settings = getattr(handler, "home_assistant_settings", None)
        return bool(settings is not None and getattr(settings, "enabled", False))
    return getattr(handler, "canonical_execution", None) is not None


def failure_recovery_guidance(action: str, error: str) -> str | None:
    key = (str(action or ""), str(error or ""))
    guidance = {
        ("alerts", "alerts_unavailable"): "Try naming the timer, alarm, or reminder and include its time or duration.",
        ("alerts", "alert_delivery_target_unavailable"): "Try again from an enabled alert-capable satellite.",
        ("calculation", "calculation_unavailable"): "Try a bounded expression such as ‘what is 15 percent of 80?’",
        ("temporal", "temporal_unavailable"): "Try naming a supported place, time zone, or explicit date.",
    }
    return guidance.get(key)
