from __future__ import annotations

import re
from dataclasses import dataclass

from oracle_app.alerts import parse_duration


@dataclass(frozen=True)
class AudiobookIntent:
    intent: str
    title: str | None = None
    series: str | None = None
    ordinal: int | None = None
    narrator_preference: str | None = None
    sleep_timer_seconds: int | None = None
    original_text: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "title": self.title,
            "series": self.series,
            "ordinal": self.ordinal,
            "narrator_preference": self.narrator_preference,
            "sleep_timer_seconds": self.sleep_timer_seconds,
            "original_text": self.original_text,
        }


def is_audiobook_request(text: str) -> bool:
    return parse_audiobook_intent(text) is not None


def parse_audiobook_intent(text: str) -> AudiobookIntent | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    series_lookup = _parse_series_lookup_intent(normalized)
    if series_lookup is not None:
        return series_lookup

    series_play = _parse_series_play_intent(normalized)
    if series_play is not None:
        return series_play

    resume_current_target, resume_current_sleep_timer_seconds = _split_sleep_timer_suffix(normalized)
    if resume_current_target in {
        "resume my book",
        "resume my audiobook",
        "continue my book",
        "continue my audiobook",
        "pick up my book where i left off",
        "pick up my audiobook where i left off",
        "pick up the book where i left off",
        "pick up the audiobook where i left off",
        "read my book",
        "read my audiobook",
        "read the book",
        "read the audiobook",
        "resume book",
        "resume audiobook",
        "continue book",
        "continue audiobook",
    }:
        return AudiobookIntent(
            intent="resume_current",
            sleep_timer_seconds=resume_current_sleep_timer_seconds,
            original_text=normalized,
        )

    if normalized in {
        "pause book",
        "pause audiobook",
        "pause my book",
        "pause my audiobook",
        "pause the book",
        "pause the audiobook",
    }:
        return AudiobookIntent(intent="pause", original_text=normalized)

    if normalized in {
        "stop book",
        "stop audiobook",
        "stop my book",
        "stop my audiobook",
        "stop the book",
        "stop the audiobook",
    }:
        return AudiobookIntent(intent="stop", original_text=normalized)

    if normalized in {
        "resume book",
        "resume audiobook",
        "resume the book",
        "resume the audiobook",
    }:
        return AudiobookIntent(intent="resume", original_text=normalized)

    if normalized in {
        "what book is playing",
        "what audiobook is playing",
        "what book am i on",
        "what audiobook am i on",
        "what book am i listening to",
        "what audiobook am i listening to",
    }:
        return AudiobookIntent(intent="what_is_playing", original_text=normalized)

    for prefix in (
        "play audiobook ",
        "play book ",
        "play the audiobook ",
        "play the book ",
        "start audiobook ",
        "start book ",
        "queue up audiobook ",
        "queue up book ",
        "cue up audiobook ",
        "cue up book ",
        "put on audiobook ",
        "put on book ",
        "listen to audiobook ",
        "listen to book ",
    ):
        if normalized.startswith(prefix):
            remainder = normalized[len(prefix) :].strip()
            title, sleep_timer_seconds = _split_sleep_timer_suffix(remainder)
            if not title:
                title = remainder
            if title:
                parsed_title, narrator_preference = _extract_narrator_preference(title)
                return AudiobookIntent(
                    intent="play",
                    title=parsed_title,
                    narrator_preference=narrator_preference,
                    sleep_timer_seconds=sleep_timer_seconds,
                    original_text=normalized,
                )

    for prefix in ("read ", "start reading "):
        if normalized.startswith(prefix):
            remainder = normalized[len(prefix) :].strip()
            if _looks_like_non_audiobook_read_request(remainder):
                return None
            if remainder and remainder not in {"my book", "my audiobook", "the book", "the audiobook"}:
                title, sleep_timer_seconds = _split_sleep_timer_suffix(remainder)
                if not title:
                    title = remainder
                parsed_title, narrator_preference = _extract_narrator_preference(title)
                return AudiobookIntent(
                    intent="play",
                    title=parsed_title,
                    narrator_preference=narrator_preference,
                    sleep_timer_seconds=sleep_timer_seconds,
                    original_text=normalized,
                )

    pickup_match = re.match(
        r"^pick up (?P<title>.+?) where i left off$",
        normalized,
    )
    if pickup_match is not None:
        title = pickup_match.group("title").strip()
        if title and title not in {"my book", "my audiobook", "the book", "the audiobook"}:
            parsed_title, narrator_preference = _extract_narrator_preference(title)
            return AudiobookIntent(
                intent="play",
                title=parsed_title,
                narrator_preference=narrator_preference,
                original_text=normalized,
            )

    return None


def _looks_like_non_audiobook_read_request(remainder: str) -> bool:
    normalized = " ".join(str(remainder).strip().lower().split())
    if not normalized or not normalized.startswith(("me ", "us ")):
        return False
    non_audiobook_markers = (
        " news",
        " headline",
        " headlines",
        " latest from ",
        " something from ",
        " from npr",
        " from reuters",
        " from associated press",
        " from ap",
    )
    wrapped = f" {normalized} "
    return any(marker in wrapped for marker in non_audiobook_markers)


def _extract_narrator_preference(title: str) -> tuple[str, str | None]:
    normalized = " ".join(str(title).strip().lower().split())
    if not normalized:
        return "", None

    patterns = (
        r"^(?:the )?(?P<narrator>[a-z0-9 .'\-]+?) version of (?P<title>.+)$",
        r"^(?P<title>.+?) narrated by (?P<narrator>[a-z0-9 .'\-]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        parsed_title = match.group("title").strip(" ,")
        narrator = match.group("narrator").strip(" ,")
        if parsed_title and narrator:
            return parsed_title, narrator
    return normalized, None


def parse_bare_audiobook_sleep_timer_intent(text: str) -> AudiobookIntent | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized or not _looks_like_standalone_sleep_timer_request(normalized):
        return None
    return _parse_sleep_timer_intent(normalized)


def _parse_series_lookup_intent(normalized: str) -> AudiobookIntent | None:
    patterns = (
        r"^what\s+book\s+(?P<ordinal>\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+of\s+(?:the\s+)?(?P<series>.+?)\s+series\s+(?:is|was)$",
        r"^what\s+is\s+book\s+(?P<ordinal>\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+of\s+(?:the\s+)?(?P<series>.+?)\s+series$",
        r"^which\s+book\s+is\s+(?P<ordinal>\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+in\s+(?:the\s+)?(?P<series>.+?)\s+series$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        ordinal = _parse_ordinal_value(match.group("ordinal"))
        series = match.group("series").strip(" ,")
        if ordinal is None or not series:
            continue
        return AudiobookIntent(
            intent="series_lookup",
            title=series,
            series=series,
            ordinal=ordinal,
            original_text=normalized,
        )
    return None


def _parse_series_play_intent(normalized: str) -> AudiobookIntent | None:
    ordinal_pattern = (
        r"(?P<ordinal>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    )
    patterns = (
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+(?:the\s+)?{ordinal_pattern}\s+(?P<series>.+?)\s+book$",
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+(?P<series>.+?)\s+book\s+{ordinal_pattern}$",
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+(?:the\s+)?{ordinal_pattern}\s+book\s+in\s+(?:the\s+)?(?P<series>.+)$",
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+book\s+{ordinal_pattern}\s+of\s+(?:the\s+)?(?P<series>.+)$",
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+book\s+{ordinal_pattern}\s+in\s+(?:the\s+)?(?P<series>.+)$",
        rf"^(?:play|start|queue up|cue up|read|listen to)\s+(?:the\s+)?book\s+{ordinal_pattern}\s+of\s+(?:the\s+)?(?P<series>.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        ordinal = _parse_ordinal_value(match.group("ordinal"))
        series = match.group("series").strip(" ,")
        if ordinal is None or not series:
            continue
        return AudiobookIntent(
            intent="play",
            title=series,
            series=series,
            ordinal=ordinal,
            original_text=normalized,
        )
    return None


def _parse_ordinal_value(raw: str) -> int | None:
    value = str(raw).strip().lower()
    if value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    number_words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    return number_words.get(value)


def _looks_like_standalone_sleep_timer_request(normalized: str) -> bool:
    if not _contains_sleep_timer_words(normalized):
        return False
    return normalized.startswith(
        (
            "set ",
            "start ",
            "add ",
            "cancel ",
            "clear ",
            "remove ",
            "delete ",
            "what ",
            "do i have ",
            "is there ",
            "when is ",
            "when does ",
            "how much ",
            "how long ",
        )
    ) or normalized in {"sleep timer", "cancel sleep timer"} or _parse_sleep_timer_intent(normalized) is not None


def _contains_sleep_timer_words(normalized: str) -> bool:
    return "sleep timer" in normalized or ("sleep" in normalized and "timer" in normalized)


def _parse_sleep_timer_intent(normalized: str) -> AudiobookIntent | None:
    if any(token in normalized for token in ("cancel", "clear", "remove", "delete")):
        return AudiobookIntent(intent="sleep_timer_cancel", original_text=normalized)

    if any(
        phrase in normalized
        for phrase in (
            "what sleep timer",
            "sleep timer status",
            "status of my sleep timer",
            "is a sleep timer set",
            "do i have a sleep timer",
            "when is my sleep timer",
            "when does my sleep timer go off",
            "how much time",
            "how long",
            "time left",
            "remaining",
        )
    ):
        return AudiobookIntent(intent="sleep_timer_status", original_text=normalized)

    duration_seconds = parse_duration(normalized)
    if duration_seconds is None:
        return None
    return AudiobookIntent(
        intent="sleep_timer",
        sleep_timer_seconds=duration_seconds,
        original_text=normalized,
    )


def _split_sleep_timer_suffix(text: str) -> tuple[str, int | None]:
    normalized = " ".join(text.strip().lower().split())
    patterns = (
        r"^(?P<title>.+?)\s+(?:and|with)\s+(?:set\s+)?(?:a\s+)?sleep timer for (?P<duration>.+)$",
        r"^(?P<title>.+?),\s*(?:set\s+)?(?:a\s+)?sleep timer for (?P<duration>.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        title = match.group("title").strip(" ,")
        duration_seconds = parse_duration(match.group("duration"))
        if title and duration_seconds is not None:
            return title, duration_seconds
    return normalized, None
