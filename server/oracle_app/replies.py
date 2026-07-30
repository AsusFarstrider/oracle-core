from __future__ import annotations

from datetime import datetime
import re

from .schemas import DispatchPlan


DEFAULT_REPLY = "Done."


def _extract_home_assistant_speech(payload: dict) -> str:
    response = payload.get("response") or {}
    speech = response.get("speech") or {}
    plain = speech.get("plain") or {}
    return str(plain.get("speech", "")).strip()


def build_reply_text(dispatch: DispatchPlan) -> str:
    result = dispatch.result or {}

    if dispatch.status == "pending_confirmation":
        return str(result.get("prompt", "Please confirm before I proceed.")).strip()
    if dispatch.status == "pending_clarification":
        return str(result.get("prompt", "I found multiple matches. Which one did you want?")).strip()

    if dispatch.target == "home_assistant":
        if dispatch.status == "failed":
            error = str(result.get("error", "")).strip()
            if error == "pending_state_requires_context":
                return "I can't safely continue that request without source and session context."
            if error == "home_room_unresolved":
                return "I don't know which room to use for that request."
            if error == "retired_home_room_name":
                return "That room name is no longer active in Oracle."
            if error == "home_assistant_target_unavailable":
                unavailable_targets = result.get("unavailable_targets") or []
                if isinstance(unavailable_targets, list) and unavailable_targets:
                    first = unavailable_targets[0]
                    name = str((first or {}).get("name") or "").strip()
                    if name:
                        return f"{name} is unavailable right now."
                return "That device is unavailable right now."
            if error == "home_assistant_state_verification_failed":
                verification_failed_targets = result.get("verification_failed_targets") or []
                if isinstance(verification_failed_targets, list) and verification_failed_targets:
                    first = verification_failed_targets[0]
                    name = str((first or {}).get("name") or "").strip()
                    expected_state = str((first or {}).get("expected_state") or "").strip().lower()
                    expected_attribute = str((first or {}).get("expected_attribute") or "").strip().lower()
                    expected_description = str((first or {}).get("expected_description") or "").strip()
                    if name and expected_state == "on":
                        return f"{name} did not turn on."
                    if name and expected_state == "off":
                        return f"{name} did not turn off."
                    if name and expected_state == "open":
                        return f"{name} did not open."
                    if name and expected_state == "closed":
                        return f"{name} did not close."
                    if name and expected_state == "locked":
                        return f"{name} did not lock."
                    if name and expected_state == "unlocked":
                        return f"{name} did not unlock."
                    if name and expected_attribute == "temperature" and expected_description:
                        return f"{name} did not reach {expected_description}."
                    if name and expected_attribute == "brightness" and expected_description:
                        return f"{name} did not reach {expected_description}."
                return "That device did not reach the expected state."
            return "I couldn't complete that request."
        return _extract_home_assistant_speech(result) or DEFAULT_REPLY

    if dispatch.target == "calendar":
        if dispatch.status == "failed":
            error = str(result.get("error", "")).strip()
            if error == "calendar_query_failed":
                return "I couldn't read your calendar right now."
            if error == "calendar_write_unavailable":
                return "I can't add calendar events right now."
            if error == "calendar_write_failed":
                return "I couldn't add that to the calendar."
            return "I couldn't complete that calendar request."
        stale_notice = str(result.get("stale_notice") or "").strip()
        action = str(result.get("action", "")).strip()
        events = result.get("events") or []
        if action == "commit_event":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "Okay, I added it."
        if action == "find_event":
            if not events:
                return "I couldn't find that on your calendar."
            event = events[0]
            summary = str(event.get("summary", "")).strip()
            start = _format_calendar_start(event)
            if summary and start:
                answer = f"{summary} is {start}."
                return f"{stale_notice} {answer}".strip()
            return "I found it on your calendar."
        if action == "list_events":
            if not events:
                return "You have nothing on your calendar for that time."
            spoken = [_format_calendar_event_brief(event) for event in events]
            spoken = [item for item in spoken if item]
            if not spoken:
                return "I couldn't find anything speakable on your calendar for that time."
            count = len(spoken)
            thing_word = "thing" if count == 1 else "things"
            answer = f"You have {count} {thing_word} on your calendar. " + " ".join(spoken)
            return f"{stale_notice} {answer}".strip()

    if dispatch.target == "facts":
        summary = str(result.get("summary") or "").strip()
        if bool(result.get("summarized_by_model")) and summary:
            return summary
        status = str(result.get("facts_status") or "").strip()
        if status == "answered":
            answer = result.get("answer") or {}
            text = str((answer or {}).get("text") or "").strip() if isinstance(answer, dict) else ""
            return _select_facts_reply_sentence(str(result.get("query") or ""), text) or "I found an answer, but couldn't read it correctly."
        if status == "evidence_only":
            evidence = result.get("evidence") or []
            if isinstance(evidence, list) and evidence:
                first = evidence[0] if isinstance(evidence[0], dict) else {}
                snippet = str(first.get("snippet") or "").strip()
                if snippet:
                    return _select_facts_reply_sentence(str(result.get("query") or ""), snippet) or snippet
            return "I found related information, but not enough to answer confidently."
        if status == "no_result":
            return "I couldn't find a good answer to that."
        if status == "disabled":
            return "Fact lookup is not configured yet."
        if status == "provider_error" or dispatch.status == "failed":
            return "I couldn't look that up right now."
        return "I couldn't answer that right now."

    if dispatch.target == "fallback_router":
        if dispatch.status == "failed":
            return "I'm sorry, I didn't understand what you said."
        return "I'm sorry, I didn't understand what you said."

    if dispatch.target == "audiobook":
        action = str(result.get("action", "")).strip()
        if dispatch.status == "failed":
            error = str(result.get("error", "")).strip()
            if error == "unknown_user":
                requested = str(result.get("requested_user_name", "")).strip()
                if requested:
                    return f"I don't know a user named {requested}."
                return "I don't know which user to use for that audiobook request."
            if error == "audiobook_user_not_configured":
                requested = str(result.get("requested_user_name") or result.get("user_id") or "").strip()
                if requested:
                    return f"{requested.capitalize()} is not configured for audiobooks yet."
                return "That user is not configured for audiobooks yet."
            if error == "pending_state_requires_context":
                return "I can't safely continue that audiobook request without source and session context."
            if error == "audiobook_not_found":
                return "I couldn't find that audiobook."
            if error == "audiobook_search_failed":
                return "I couldn't search Audiobookshelf right now."
            if error == "satellite_command_failed":
                return "I couldn't reach the playback satellite."
            if error == "no_active_audiobook":
                return "No audiobook is playing right now."
            return "I couldn't complete that audiobook request."
        if action in {"play", "resume_current"}:
            selected = result.get("selected") or {}
            title = str(selected.get("title", "")).strip()
            author = str(selected.get("author", "")).strip()
            start_position = float(selected.get("start_position_seconds") or 0)
            base_reply = ""
            if title and author and start_position > 30:
                base_reply = f"Resuming {title} by {author}."
            elif title and author:
                base_reply = f"Playing {title} by {author}."
            elif title:
                base_reply = f"Playing {title}."
            sleep_timer = result.get("sleep_timer") or {}
            duration_speech = str(sleep_timer.get("duration_speech", "")).strip()
            if base_reply and duration_speech:
                return f"{base_reply} Sleep timer set for {duration_speech}."
            if base_reply:
                return base_reply
        if action == "pause":
            return "Paused your audiobook."
        if action == "resume":
            return "Resuming your audiobook."
        if action == "stop":
            return "Stopped your audiobook."
        if action == "what_is_playing":
            now_playing = result.get("now_playing") or {}
            title = str(now_playing.get("title", "")).strip()
            author = str(now_playing.get("author", "")).strip()
            if title and author:
                return f"You're listening to {title} by {author}."
            if title:
                return f"You're listening to {title}."
            return "No audiobook is playing right now."
        if action == "series_lookup":
            match = result.get("match") or {}
            title = str(match.get("title", "")).strip()
            author = str(match.get("author", "")).strip()
            ordinal = int(result.get("ordinal") or 0)
            if title and ordinal > 0:
                if author:
                    return f"Book {ordinal} is {title} by {author}."
                return f"Book {ordinal} is {title}."
            return "I couldn't find that series entry."
        if action == "sleep_timer":
            operation = str(result.get("operation", "")).strip()
            if operation == "create":
                duration_speech = str(result.get("duration_speech", "")).strip()
                if duration_speech:
                    return f"Sleep timer set for {duration_speech}."
                return "Sleep timer set."
            if operation == "cancel":
                count = int(result.get("count") or 0)
                if count <= 0:
                    return "There is no active audiobook sleep timer."
                return "Canceled the audiobook sleep timer."
            if operation == "status":
                count = int(result.get("count") or 0)
                if count <= 0:
                    return "There is no active audiobook sleep timer."
                due_at_text = str(result.get("due_at", "")).strip()
                if due_at_text:
                    try:
                        due_at = datetime.fromisoformat(due_at_text)
                        remaining_seconds = max(0.0, (due_at - datetime.now().astimezone()).total_seconds())
                        return f"The audiobook sleep timer has {_format_duration(remaining_seconds)} remaining."
                    except ValueError:
                        pass
                return "The audiobook sleep timer is active."

    if dispatch.target == "news":
        if dispatch.status == "failed":
            return "I couldn't get the latest headlines right now."
        headlines = result.get("headlines") or []
        source_label = str(result.get("source_label", "the news")).strip()
        if not headlines:
            return f"I couldn't find any current headlines from {source_label}."
        spoken = [str(item.get("title", "")).strip() for item in headlines[:3] if str(item.get("title", "")).strip()]
        if not spoken:
            return f"I couldn't find any current headlines from {source_label}."
        stale_notice = str(result.get("stale_notice") or "").strip()
        if len(spoken) == 1:
            answer = f"From {source_label}: {spoken[0]}."
            return f"{stale_notice} {answer}".strip()
        if len(spoken) == 2:
            answer = f"From {source_label}: {spoken[0]}. Also, {spoken[1]}."
            return f"{stale_notice} {answer}".strip()
        answer = f"From {source_label}: {spoken[0]}. Also, {spoken[1]}. And {spoken[2]}."
        return f"{stale_notice} {answer}".strip()

    if dispatch.target == "network":
        if dispatch.status == "failed":
            return "I couldn't check the network right now."
        speech = str(result.get("speech", "")).strip()
        if speech:
            return speech
        return "I couldn't check the network right now."

    if dispatch.target == "system":
        if dispatch.status == "failed":
            action = str(result.get("action", "")).strip()
            if action == "confirm_pending":
                confirmed = result.get("confirmed_dispatch") or {}
                confirmed_target = str(confirmed.get("target") or "").strip()
                if confirmed_target:
                    nested = DispatchPlan(
                        target=confirmed_target,
                        hook=str(confirmed.get("hook") or ""),
                        payload=dict(confirmed.get("payload") or {}),
                        status=str(confirmed.get("status") or "failed"),
                        result=dict(confirmed.get("result") or {}),
                    )
                    return build_reply_text(nested)
            error = str(result.get("error", "")).strip()
            if error == "unknown_user":
                requested = str(result.get("requested_user_name", "")).strip()
                if requested:
                    return f"I don't know a user named {requested}."
                return "I don't know which user to switch to."
            return "I couldn't complete that request."
        action = result.get("action")
        if action == "ignore":
            return ""
        if action == "refresh_cache":
            return "My device cache has been refreshed."
        if action == "cancel_pending":
            return "Canceled."
        if action == "switch_user":
            display_name = str(result.get("display_name", "")).strip()
            if display_name:
                return f"Okay, using {display_name}."
            return "Okay, switched users."
        if action == "calculation":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not calculate that right now."
        if action == "alerts":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not manage that timer, alarm, or reminder right now."
        if action in {"current_time", "current_date", "current_time_date"}:
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not get the current time or date right now."
        confirmed = result.get("confirmed_dispatch") or {}
        confirmed_target = str(confirmed.get("target") or "").strip()
        if confirmed_target:
            nested = DispatchPlan(
                target=confirmed_target,
                hook=str(confirmed.get("hook") or ""),
                payload=dict(confirmed.get("payload") or {}),
                status=str(confirmed.get("status") or "failed"),
                result=dict(confirmed.get("result") or {}),
            )
            return build_reply_text(nested)
        confirmed_result = confirmed.get("result") or {}
        return _extract_home_assistant_speech(confirmed_result) or "Confirmed."

    if dispatch.target == "weather":
        if dispatch.status == "failed":
            error = str(result.get("error", "")).strip()
            if error == "weather_unavailable":
                return "I could not get current weather right now."
            if error == "remote_weather_location_unresolved":
                detail = str(result.get("detail", "")).strip()
                if detail:
                    return detail
                return "I couldn't resolve that location."
            if error == "forecast_out_of_range":
                detail = str(result.get("detail", "")).strip()
                if detail:
                    return detail
                return "That time is outside the current forecast window."
            if error == "remote_forecast_out_of_range":
                detail = str(result.get("detail", "")).strip()
                if detail:
                    return detail
                return "That time is outside the current forecast window for that location."
            if error == "remote_weather_unavailable":
                return "I could not get the weather for that location right now."
            if error == "forecast_unavailable":
                return "I could not get the forecast right now."
            if error == "weather_history_unavailable":
                return "I could not get that historical weather right now."
            return "I couldn't complete that weather request."
        action = result.get("action")
        if action in {"current_weather", "remote_current_weather"}:
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            if action == "remote_current_weather":
                return "I could not get the weather for that location right now."
            return "I could not get current weather right now."
        if action == "weather_forecast":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not get the forecast right now."
        if action == "remote_weather_forecast":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not get the forecast for that location right now."
        if action == "weather_history":
            speech = str(result.get("speech", "")).strip()
            if speech:
                return speech
            return "I could not get that historical weather right now."
        return "I couldn't complete that weather request."

    if dispatch.target == "music":
        action = str(result.get("action", "")).strip()
        if dispatch.status == "failed":
            error = str(result.get("error", "")).strip()
            if error == "pending_state_requires_context":
                return "I can't safely continue that music request without source and session context."
            if error == "music_not_found":
                return "I couldn't find that in Plex."
            if error == "satellite_command_failed":
                return "I couldn't reach the playback satellite."
            if error in {"music_search_failed", "plex_search_failed"}:
                return "I couldn't search Plex right now."
            return "I couldn't complete that music request."
        if action == "what_is_playing":
            now_playing = result.get("now_playing") or {}
            title = str(now_playing.get("title", "")).strip()
            artist = str(now_playing.get("artist", "")).strip()
            album = str(now_playing.get("album", "")).strip()
            if title and artist:
                if album:
                    return f"You're listening to {title} by {artist} from {album}."
                return f"You're listening to {title} by {artist}."
            return "Nothing is playing right now."
        if action == "lookup_album":
            reply = str(result.get("reply", "")).strip()
            if reply:
                return reply
            selected = result.get("selected") or {}
            title = str(selected.get("title", "")).strip()
            artist = str(selected.get("artist", "")).strip()
            album = str(selected.get("album", "")).strip()
            if title and artist and album:
                return f"{title} is on {album} by {artist}."
            if title and album:
                return f"{title} is on {album}."
            return "I couldn't find that in Plex."
        if action == "play":
            selected = result.get("selected") or {}
            media_type = str(selected.get("type") or selected.get("media_type") or "").strip().lower()
            title = str(selected.get("title", "")).strip()
            artist = str(selected.get("artist", "")).strip()
            if media_type == "artist":
                if title:
                    return f"Playing songs by {title}."
                if artist:
                    return f"Playing songs by {artist}."
            if media_type == "album":
                if title and artist:
                    return f"Playing the album {title} by {artist}."
                if title:
                    return f"Playing the album {title}."
            if media_type == "playlist":
                if title:
                    return f"Playing the playlist {title}."
            if title and artist:
                return f"Playing {title} by {artist}."
            if title:
                return f"Playing {title}."
        if action in {"pause", "resume", "stop", "next", "previous", "restart"}:
            if action == "pause" and str(result.get("degraded_state_fallback", "")).strip() == "dual_active_pause_all":
                return "Pausing all active media."
            if action == "stop" and str(result.get("degraded_state_fallback", "")).strip() == "dual_active_stop_all":
                return "Stopping all active media."
            replies = {
                "pause": "Paused.",
                "resume": "Resumed.",
                "stop": "Stopped.",
                "next": "Skipping.",
                "previous": "Going back.",
                "restart": "Restarting.",
            }
            return replies.get(action, DEFAULT_REPLY)
        if action == "set_volume":
            satellite = result.get("satellite") or {}
            level = satellite.get("volume_level")
            if isinstance(level, int):
                return f"Volume set to {level} percent."
            return "Volume updated."
        if action == "volume_up":
            return "Turning it up."
        if action == "volume_down":
            return "Turning it down."

    if dispatch.status == "failed":
        return "I couldn't complete that request."

    return DEFAULT_REPLY


def _format_duration(total_seconds: float) -> str:
    seconds = max(0, int(round(total_seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
    if minutes:
        parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
    if secs and not hours:
        parts.append(f"{secs} {'second' if secs == 1 else 'seconds'}")
    return ", ".join(parts) if parts else "0 seconds"


def _format_calendar_start(event: dict, *, include_prefix: bool = True) -> str:
    start_raw = str(event.get("start", "")).strip()
    all_day = bool(event.get("all_day"))
    if not start_raw:
        return ""
    try:
        from datetime import datetime

        start = datetime.fromisoformat(start_raw)
    except ValueError:
        return ""
    if all_day:
        return start.strftime("on %A, %B %-d") if include_prefix else start.strftime("%A, %B %-d")
    spoken_time = _format_spoken_time(start)
    if include_prefix:
        return f"on {start.strftime('%A')} at {spoken_time}"
    return spoken_time


def _format_spoken_time(start) -> str:
    hour = start.strftime("%-I")
    minute = start.minute
    meridiem = start.strftime("%p")
    if minute == 0:
        return f"{hour} {meridiem}"
    return f"{hour}:{minute:02d} {meridiem}"


def _format_calendar_event_brief(event: dict) -> str:
    summary = _shorten_calendar_summary(str(event.get("summary", "")))
    if not summary:
        return ""
    all_day = bool(event.get("all_day"))
    if all_day:
        return f"All day, {summary}."
    start = _format_calendar_start(event, include_prefix=False)
    if start:
        return f"At {start}, {summary}."
    return f"{summary}."


def _shorten_calendar_summary(value: str, *, max_words: int = 6, max_chars: int = 48) -> str:
    summary = " ".join(str(value).strip().split())
    if not summary:
        return ""
    words = summary.split()
    if len(words) > max_words:
        summary = " ".join(words[:max_words]) + "..."
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


_FACTS_STOP_WORDS = {
    "about",
    "after",
    "also",
    "are",
    "can",
    "could",
    "does",
    "for",
    "from",
    "how",
    "into",
    "long",
    "many",
    "much",
    "the",
    "that",
    "this",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "with",
}

_FACTS_ABBREVIATIONS = (
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "Sr.",
    "Jr.",
    "St.",
    "U.S.",
    "U.K.",
    "e.g.",
    "i.e.",
    "vs.",
)


def _select_facts_reply_sentence(query: str, text: str) -> str:
    sentences = _split_facts_sentences(text)
    if not sentences:
        return ""
    intent = _facts_query_intent(query)
    if intent:
        intent_terms = _facts_intent_terms(intent)
        for sentence in sentences:
            normalized_sentence = _normalize_facts_text(sentence)
            if any(term in normalized_sentence for term in intent_terms):
                if intent == "location":
                    return _trim_location_fact_sentence(sentence)
                return sentence
    query_terms = _facts_query_terms(query)
    if query_terms:
        scored = sorted(
            ((len(query_terms & set(_normalize_facts_text(sentence).split())), index, sentence) for index, sentence in enumerate(sentences)),
            key=lambda item: (-item[0], item[1]),
        )
        if scored and scored[0][0] > 0:
            return scored[0][2]
    return sentences[0]


def _split_facts_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return []
    protected = normalized
    placeholders: dict[str, str] = {}
    for index, abbreviation in enumerate(_FACTS_ABBREVIATIONS):
        placeholder = f"__FACTS_ABBR_{index}__"
        placeholders[placeholder] = abbreviation
        protected = protected.replace(abbreviation, abbreviation.replace(".", placeholder))
    protected = re.sub(
        r"\b([A-Z])\.",
        lambda match: f"{match.group(1)}__FACTS_INITIAL__",
        protected,
    )
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", protected)
    sentences: list[str] = []
    for piece in pieces:
        restored = piece.replace("__FACTS_INITIAL__", ".")
        for placeholder, abbreviation in placeholders.items():
            restored = restored.replace(placeholder, ".")
            restored = restored.replace(abbreviation.replace(".", "."), abbreviation)
        restored = restored.strip()
        if restored:
            sentences.append(restored)
    return sentences


def _facts_query_intent(query: str) -> str:
    normalized = _normalize_facts_text(query)
    if "life span" in normalized or "lifespan" in normalized or "life expectancy" in normalized:
        return "lifespan"
    if re.search(r"\bhow long\b.*\b(live|lives|living)\b", normalized):
        return "lifespan"
    if re.search(r"\bhow old\b.*\b(get|gets|live|lives)\b", normalized):
        return "lifespan"
    if "who wrote" in normalized or "author of" in normalized:
        return "authorship"
    if normalized.startswith("where is ") or normalized.startswith("where are ") or " located" in normalized:
        return "location"
    return ""


def _facts_intent_terms(intent: str) -> tuple[str, ...]:
    if intent == "lifespan":
        return ("lifespan", "life span", "life expectancy", "live", "lives", "lived", "years", "oldest")
    if intent == "authorship":
        return ("written by", "author", "wrote")
    if intent == "location":
        return (" located ", " situated ", " in ", " near ")
    return ()


def _trim_location_fact_sentence(sentence: str) -> str:
    trimmed = str(sentence or "").strip()
    if not trimmed:
        return ""
    for marker in (
        ", above ",
        ", along ",
        ", which ",
        " above ",
        " along ",
        " which ",
        " at an elevation ",
        " on a mountain ridge",
        " at 2,",
    ):
        index = trimmed.find(marker)
        if index > 0:
            trimmed = trimmed[:index].rstrip(" ,;")
            break
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."
    return trimmed


def _facts_query_terms(query: str) -> set[str]:
    return {
        word
        for word in _normalize_facts_text(query).split()
        if len(word) > 3 and word not in _FACTS_STOP_WORDS
    }


def _normalize_facts_text(value: str) -> str:
    normalized = str(value or "").lower().strip()
    normalized = re.sub(r"[\u2018\u2019]", "'", normalized)
    normalized = re.sub(r"[\u201c\u201d]", '"', normalized)
    normalized = re.sub(r"[^a-z0-9' ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
