from __future__ import annotations
import re

from .base import CapabilityDecision
from oracle_app import state
from oracle_app.audiobook import (
    is_audiobook_request,
    parse_audiobook_intent,
)
from oracle_app.audiobook_runtime.pending import (
    looks_like_pending_audiobook_clarification,
)
from oracle_app.calendar import is_calendar_request
from oracle_app.calendar_write import parse_calendar_write_request
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.music_runtime.parsing import (
    is_music_request,
    parse_music_intent,
)
from oracle_app.music_runtime.policy import is_generic_title_only_play_intent
from oracle_app.music_runtime.pending import (
    looks_like_pending_music_clarification,
)
from oracle_app.network import is_network_request
from oracle_app.news import is_news_request
from oracle_app.configuration.information_runtime_settings import NewsRuntimeSettings
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings
from oracle_app.room_context import canonical_pending_room_reply_name, canonical_room_name
from oracle_app.routing_helpers import (
    canonicalize_home_command,
    detect_implied_home_command,
    has_home_keyword,
)
from oracle_app.system_intents import classify_system_intent
from oracle_app.weather_intents import classify_weather_intent


_AUDIOBOOK_TITLE_CUES = (
    "book ",
    " volume ",
    " chapter ",
    " full cast",
    " regular ",
    " standard ",
    " normal ",
    " unabridged",
    " abridged",
    " edition",
    " version of ",
    " narrated by ",
)
_EXPLICIT_MUSIC_CUES = (
    "soundtrack",
    "album ",
    "playlist",
    "artist ",
    "songs from ",
    "music from ",
    "songs by ",
    "music by ",
    "something by ",
)
_PROBABLE_AUDIOBOOK_PLAY_PREFIXES = (
    "play ",
    "start ",
    "queue up ",
    "cue up ",
    "put on ",
    "listen to ",
    "i want to hear ",
    "i wanna hear ",
)


def _looks_like_probable_audiobook_title(title: str) -> bool:
    normalized = " ".join(str(title).strip().lower().split())
    if not normalized:
        return False
    if any(cue in f" {normalized} " for cue in _AUDIOBOOK_TITLE_CUES):
        return True
    tokens = normalized.split()
    if len(tokens) < 5:
        return False
    if re.search(r"\band the\b", normalized) and re.search(r"\bof\b", normalized):
        return True
    return False


def _has_explicit_music_cue(original_text: str) -> bool:
    normalized = f" {' '.join(str(original_text).strip().lower().split())} "
    return any(cue in normalized for cue in _EXPLICIT_MUSIC_CUES)


def _extract_probable_audiobook_title_from_play_request(text: str) -> str | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None
    for prefix in _PROBABLE_AUDIOBOOK_PLAY_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix) :].strip()
        if not remainder:
            return None
        if remainder.startswith(("some ", "the song ", "song ", "the track ", "track ")):
            return None
        return remainder
    return None


class SystemCommandCapability:
    name = "system_commands"
    priority = 100

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action not in {"confirm_pending", "cancel_pending", "refresh_cache", "switch_user"}:
            return None
        return CapabilityDecision(
            target="system",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class PendingConfirmationCapability:
    name = "pending_confirmation"
    priority = 99

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        pending = state.load_pending_confirmation(source, session_id)
        if pending is None:
            return None
        if normalized_text not in {"yes", "yes please", "add it", "do it"}:
            return None
        return CapabilityDecision(
            target="system",
            confidence=0.99,
            reason="Matched pending confirmation context",
            normalized_text="confirm",
        )


class ImpliedHomeCapability:
    name = "implied_home"
    priority = 80

    def __init__(self, household_settings: HouseholdRuntimeSettings | None = None) -> None:
        self.household_settings = household_settings

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        implied_command = detect_implied_home_command(
            normalized_text,
            household_settings=self.household_settings,
        )
        if implied_command is None:
            return None

        command, reason = implied_command
        return CapabilityDecision(
            target="home_assistant",
            confidence=0.74,
            reason=reason,
            normalized_text=command,
        )


class WeatherQueryCapability:
    name = "weather_query"
    priority = 70

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action not in {"current_weather", "remote_current_weather"}:
            return None

        return CapabilityDecision(
            target="weather",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class ForecastQueryCapability:
    name = "forecast_query"
    priority = 72

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action not in {"weather_forecast", "remote_weather_forecast"}:
            return None

        return CapabilityDecision(
            target="weather",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class HistoricalWeatherCapability:
    name = "historical_weather"
    priority = 71

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action != "weather_history":
            return None

        return CapabilityDecision(
            target="weather",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class NetworkCapability:
    name = "network"
    priority = 69

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        del source, session_id
        if not is_network_request(normalized_text):
            return None
        return CapabilityDecision(
            target="network",
            confidence=0.88,
            reason="Matched network health query",
            normalized_text=normalized_text,
        )


class TimeDateQueryCapability:
    name = "time_date_query"
    priority = 75

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action not in {"current_time", "current_date", "current_time_date"}:
            return None

        return CapabilityDecision(
            target="system",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class MathAndConversionCapability:
    name = "math_and_conversion"
    priority = 74

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action != "calculation":
            return None

        return CapabilityDecision(
            target="system",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )


class AlertsCapability:
    name = "alerts"
    priority = 76

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action != "alerts":
            return None

        return CapabilityDecision(
            target="system",
            confidence=intent.confidence,
            reason=intent.reason,
            normalized_text=normalized_text,
        )
class KeywordHomeCapability:
    name = "keyword_home"
    priority = 60

    def __init__(self, household_settings: HouseholdRuntimeSettings | None = None) -> None:
        self.household_settings = household_settings

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        matched, keyword = has_home_keyword(normalized_text)
        if not matched or keyword is None:
            return None

        canonical = canonicalize_home_command(
            normalized_text,
            household_settings=self.household_settings,
        )
        return CapabilityDecision(
            target="home_assistant",
            confidence=0.82,
            reason=f"Matched home automation keyword: {keyword}",
            normalized_text=canonical,
        )


class CalendarCapability:
    name = "calendar"
    priority = 77

    def __init__(
        self,
        runtime_settings: CalendarRuntimeSettings | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.runtime_settings = runtime_settings
        self.canonical_authority = canonical_authority

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        if self.canonical_authority and (
            self.runtime_settings is None or not self.runtime_settings.enabled
        ):
            return None
        if not is_calendar_request(
            normalized_text,
            timezone_name=None if self.runtime_settings is None else self.runtime_settings.timezone,
        ):
            return None
        return CapabilityDecision(
            target="calendar",
            confidence=0.9,
            reason="Matched calendar request",
            normalized_text=normalized_text,
        )


class NewsCapability:
    name = "news"
    priority = 69

    def __init__(
        self,
        runtime_settings: NewsRuntimeSettings | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.runtime_settings = runtime_settings
        self.canonical_authority = canonical_authority

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        if self.canonical_authority and (
            self.runtime_settings is None or not self.runtime_settings.enabled
        ):
            return None
        if not is_news_request(
            normalized_text,
            runtime_settings=self.runtime_settings,
            canonical_authority=self.canonical_authority,
        ):
            return None
        return CapabilityDecision(
            target="news",
            confidence=0.9,
            reason="Matched news request",
            normalized_text=normalized_text,
        )


class PendingMusicCapability:
    name = "pending_music"
    priority = 89

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        pending = state.load_pending_music_request(source, session_id)
        if pending is None:
            return None
        if not normalized_text:
            return None
        if parse_music_intent(normalized_text) is not None:
            return None
        if classify_system_intent(normalized_text) is not None:
            return None
        if has_home_keyword(normalized_text)[0]:
            return None
        if not looks_like_pending_music_clarification(normalized_text, pending):
            return None
        return CapabilityDecision(
            target="music",
            confidence=0.9,
            reason="Matched pending music clarification context",
            normalized_text=normalized_text,
        )


class PendingHomeCapability:
    name = "pending_home"
    priority = 89.5

    def __init__(self, household_settings: HouseholdRuntimeSettings | None = None) -> None:
        self.household_settings = household_settings

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        pending = state.load_pending_home_request(source, session_id)
        if pending is None:
            return None
        room_name = canonical_pending_room_reply_name(
            normalized_text,
            self.household_settings,
        )
        if not room_name:
            return None
        return CapabilityDecision(
            target="home_assistant",
            confidence=0.9,
            reason="Matched pending home clarification context",
            normalized_text=normalized_text,
        )


class PendingCalendarCapability:
    name = "pending_calendar"
    priority = 89.25

    def __init__(
        self,
        news_runtime_settings: NewsRuntimeSettings | None = None,
        calendar_runtime_settings: CalendarRuntimeSettings | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.news_runtime_settings = news_runtime_settings
        self.calendar_runtime_settings = calendar_runtime_settings
        self.canonical_authority = canonical_authority

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        pending = state.load_pending_calendar_write_request(source, session_id)
        if pending is None or not normalized_text:
            return None
        if classify_system_intent(normalized_text) is not None:
            return None
        if classify_weather_intent(normalized_text) is not None:
            return None
        if is_news_request(
            normalized_text,
            runtime_settings=self.news_runtime_settings,
            canonical_authority=self.canonical_authority,
        ):
            return None
        if parse_music_intent(normalized_text) is not None:
            return None
        if parse_audiobook_intent(normalized_text) is not None:
            return None
        if has_home_keyword(normalized_text)[0]:
            return None
        if (
            is_calendar_request(
                normalized_text,
                timezone_name=(
                    None
                    if self.calendar_runtime_settings is None
                    else self.calendar_runtime_settings.timezone
                ),
            )
            or parse_calendar_write_request(normalized_text) is not None
        ):
            return None
        return CapabilityDecision(
            target="calendar",
            confidence=0.88,
            reason="Matched pending calendar clarification context",
            normalized_text=normalized_text,
        )


class PendingAudiobookCapability:
    name = "pending_audiobook"
    priority = 88

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        pending = state.load_pending_audiobook_request(source, session_id)
        if pending is None:
            return None
        if not normalized_text:
            return None
        if parse_audiobook_intent(normalized_text) is not None:
            return None
        if parse_music_intent(normalized_text) is not None:
            return None
        if classify_system_intent(normalized_text) is not None:
            return None
        if has_home_keyword(normalized_text)[0]:
            return None
        if not looks_like_pending_audiobook_clarification(normalized_text, pending):
            return None
        return CapabilityDecision(
            target="audiobook",
            confidence=0.9,
            reason="Matched pending audiobook clarification context",
            normalized_text=normalized_text,
        )


class AudiobookCapability:
    name = "audiobook"
    priority = 79

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        if not is_audiobook_request(normalized_text):
            return None
        return CapabilityDecision(
            target="audiobook",
            confidence=0.9,
            reason="Matched audiobook request",
            normalized_text=normalized_text,
        )


class ProbableAudiobookTitleCapability:
    name = "probable_audiobook_title"
    priority = 78.5

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        title: str | None = None
        intent = parse_music_intent(normalized_text)
        if intent is not None and is_generic_title_only_play_intent(intent):
            if _has_explicit_music_cue(getattr(intent, "original_text", normalized_text)):
                return None
            title = str(intent.title or "").strip()
        else:
            raw_title = _extract_probable_audiobook_title_from_play_request(normalized_text)
            if raw_title is None or _has_explicit_music_cue(normalized_text):
                return None
            title = raw_title
        if not _looks_like_probable_audiobook_title(title):
            return None
        normalized_request = f"play audiobook {title}"
        return CapabilityDecision(
            target="audiobook",
            confidence=0.84,
            reason="Matched probable audiobook title request",
            normalized_text=normalized_request,
        )


class MusicCapability:
    name = "music"
    priority = 78

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        if not is_music_request(normalized_text):
            return None
        return CapabilityDecision(
            target="music",
            confidence=0.88,
            reason="Matched music control request",
            normalized_text=normalized_text,
        )


class FactsCapability:
    name = "facts"
    priority = 10

    _QUESTION_PREFIXES = (
        "what is ",
        "what are ",
        "where is ",
        "where are ",
        "where was ",
        "where were ",
        "when is ",
        "when was ",
        "when were ",
        "when did ",
        "who is ",
        "who was ",
        "who wrote ",
        "who invented ",
        "how does ",
        "how do ",
        "how long ",
        "explain ",
    )

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = enabled

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        del source, session_id
        if self.enabled is None:
            from oracle_app.config import get_facts_settings

            enabled = bool(get_facts_settings().get("enabled", False))
        else:
            enabled = self.enabled
        if not enabled:
            return None
        if not normalized_text.startswith(self._QUESTION_PREFIXES):
            return None
        return CapabilityDecision(
            target="facts",
            confidence=0.72,
            reason="Matched factual lookup request",
            normalized_text=normalized_text,
        )


class FallbackOllamaCapability:
    name = "fallback_ollama"
    priority = 0

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        del source, session_id
        return CapabilityDecision(
            target="fallback_router",
            confidence=0.64,
            reason="No deterministic capability matched",
            normalized_text=normalized_text,
        )
