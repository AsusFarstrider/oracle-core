from __future__ import annotations

import re

from .audiobook import parse_bare_audiobook_sleep_timer_intent
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .conversation import get_conversation
from .music_runtime.control import (
    fetch_satellite_audiobook_context_session,
    fetch_satellite_audiobook_session,
    fetch_satellite_music_session,
    fetch_satellite_playback_authority,
    fetch_satellite_reply_audio_session,
)
from .music_runtime.transport import is_dual_active_music_audiobook_target, resolve_authority_transport_targets
from .room_context import canonical_room_name
from .routing_helpers import canonicalize_home_command, has_home_keyword
from .session_state import describe_followup_resolution, get_active_context
from .schemas import RouteResponse
from .tracing import log_followup_event


_PAUSE_PHRASES = {"pause", "pause it", "pause music", "pause the music", "hold on", "hold it"}
_STOP_PHRASES = {"stop", "stop it", "stop music", "stop the music"}
_RESUME_PHRASES = {"resume", "resume music", "resume the music", "continue", "continue it", "resume it"}
_NEXT_PHRASES = {"next", "skip", "skip it", "next one"}
_PREVIOUS_PHRASES = {"previous", "go back", "back"}
_RESTART_PHRASES = {"restart", "restart song", "restart track", "restart this"}
_VOLUME_UP_PHRASES = {"turn it up", "volume up", "turn the volume up", "turn the music up"}
_VOLUME_DOWN_PHRASES = {"turn it down", "volume down", "turn the volume down", "turn the music down"}
_HOME_FOLLOWUP_ACTION_PHRASES = {
    "turn it on",
    "turn them on",
    "turn it back on",
    "turn them back on",
    "turn it off",
    "turn them off",
    "turn it back off",
    "turn them back off",
    "switch it on",
    "switch them on",
    "switch it back on",
    "switch them back on",
    "switch it off",
    "switch them off",
    "switch it back off",
    "switch them back off",
    "dim it",
    "dim them",
    "brighten it",
    "brighten them",
    "make it warmer",
    "make them warmer",
    "make it cooler",
    "make them cooler",
    "lock it",
    "lock them",
    "unlock it",
    "unlock them",
}
_HOME_FOLLOWUP_ACTION_PREFIXES = (
    "set it to ",
    "set them to ",
    "make it ",
    "make them ",
    "open it",
    "open them",
    "close it",
    "close them",
)


def refine_route(
    route: RouteResponse,
    *,
    normalized_text: str,
    source: str | None = None,
    session_id: str | None = None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> RouteResponse:
    if not source or not normalized_text:
        return route

    followup = describe_followup_resolution(source, session_id)
    if str(followup.get("resolution_order") or "") == "pending_state":
        log_followup_event(
            "followup_resolution_observed",
            source=source,
            session_id=session_id,
            order="pending_state",
            route_target=str(route.target or ""),
            pending_domain=str(followup.get("pending_domain") or ""),
            detail="Skipped active-context and transport refinement because pending_state takes precedence.",
        )
        return route

    refined = _refine_active_media_transport(route, normalized_text=normalized_text, source=source)
    if refined is not None:
        log_followup_event(
            "followup_bound",
            source=source,
            session_id=session_id,
            order="active_context",
            route_target=refined.target,
            detail="Bound through live media transport refinement.",
        )
        return refined

    refined = _refine_audiobook_sleep_timer(route, normalized_text=normalized_text, source=source)
    if refined is not None:
        log_followup_event(
            "followup_bound",
            source=source,
            session_id=session_id,
            order="active_context",
            route_target=refined.target,
            detail="Bound through active audiobook sleep-timer refinement.",
        )
        return refined

    refined = _refine_session_active_context(
        route,
        normalized_text=normalized_text,
        source=source,
        session_id=session_id,
        household_settings=household_settings,
    )
    if refined is not None:
        log_followup_event(
            "followup_bound",
            source=source,
            session_id=session_id,
            order="active_context",
            route_target=refined.target,
            detail="Bound through strong active session context.",
        )
        return refined

    return route


def _refine_active_media_transport(
    route: RouteResponse,
    *,
    normalized_text: str,
    source: str,
) -> RouteResponse | None:
    action = _resolve_bare_transport_action(normalized_text)
    if action is None:
        return None

    authority_state = _safe_playback_authority(source)
    if isinstance(authority_state, dict):
        authority_targets = resolve_authority_transport_targets(action, authority_state)
        if authority_targets.get("ambiguous"):
            return None
        longform_match = authority_targets.get("audiobook", False)
        music_match = authority_targets.get("music", False)
        reply_audio_match = authority_targets.get("reply_audio", False)
        dual_active_media = bool(authority_state.get("degraded_state")) and "dual_active_music_audiobook" in [
            str(reason).strip() for reason in (authority_state.get("degraded_reasons") or [])
        ]
    else:
        longform_state = _safe_longform_state(source)
        now_playing = _safe_now_playing(source)
        reply_audio_state = _safe_reply_audio_state(source)
        longform_match = _longform_transport_matches(action, longform_state)
        music_match = _music_transport_matches(action, now_playing)
        reply_audio_match = _reply_audio_transport_matches(action, reply_audio_state)
        dual_active_media = action in {"pause", "stop"} and longform_match and music_match

    if dual_active_media and action == "pause":
        return RouteResponse(
            target="music",
            confidence=0.98,
            reason="Matched degraded dual-active media pause fallback",
            normalized_text=action,
        )

    if dual_active_media and action == "stop":
        return RouteResponse(
            target="music",
            confidence=0.98,
            reason="Matched degraded dual-active media stop fallback",
            normalized_text=action,
        )

    if longform_match:
        return RouteResponse(
            target="audiobook",
            confidence=0.97,
            reason="Matched active audiobook transport command",
            normalized_text=f"{action} audiobook",
        )

    if music_match:
        return RouteResponse(
            target="music",
            confidence=0.96,
            reason="Matched active music transport command",
            normalized_text=action,
        )

    if reply_audio_match:
        return RouteResponse(
            target="music",
            confidence=0.95,
            reason="Matched active reply audio transport command",
            normalized_text=action,
        )

    return None


def _refine_audiobook_sleep_timer(
    route: RouteResponse,
    *,
    normalized_text: str,
    source: str,
) -> RouteResponse | None:
    if not _looks_like_audiobook_sleep_timer_request(normalized_text):
        return None

    intent = parse_bare_audiobook_sleep_timer_intent(normalized_text)
    if intent is None and not _text_contains_sleep_timer_duration(normalized_text):
        return None

    if intent is not None and intent.intent in {"sleep_timer_cancel", "sleep_timer_status"}:
        return RouteResponse(
            target="audiobook",
            confidence=0.94,
            reason="Matched audiobook sleep timer request",
            normalized_text=normalized_text,
        )

    try:
        session = fetch_satellite_audiobook_context_session(source)
    except Exception:
        return route
    if not isinstance(session, dict):
        return route

    return RouteResponse(
        target="audiobook",
        confidence=0.94,
        reason="Matched active audiobook sleep timer request",
        normalized_text=normalized_text,
    )


def _looks_like_audiobook_sleep_timer_request(normalized_text: str) -> bool:
    normalized = str(normalized_text or "").strip().lower()
    return "sleep timer" in normalized or ("sleep" in normalized and "timer" in normalized)


def _text_contains_sleep_timer_duration(normalized_text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:for\s+)?(?:an?\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|forty-five|sixty)\s+(?:second|seconds|minute|minutes|hour|hours)\b",
            normalized_text,
        )
    )


def _resolve_bare_transport_action(normalized_text: str) -> str | None:
    if normalized_text in _PAUSE_PHRASES:
        return "pause"
    if normalized_text in _STOP_PHRASES:
        return "stop"
    if normalized_text in _RESUME_PHRASES:
        return "resume"
    if normalized_text in _NEXT_PHRASES:
        return "next"
    if normalized_text in _PREVIOUS_PHRASES:
        return "previous"
    if normalized_text in _RESTART_PHRASES:
        return "restart"
    if normalized_text in _VOLUME_UP_PHRASES:
        return "volume_up"
    if normalized_text in _VOLUME_DOWN_PHRASES:
        return "volume_down"
    return None


def _refine_session_active_context(
    route: RouteResponse,
    *,
    normalized_text: str,
    source: str,
    session_id: str | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> RouteResponse | None:
    active_context = get_active_context(source, session_id)
    if not isinstance(active_context, dict) or str(active_context.get("anchor_strength") or "") != "strong":
        active_context = _recover_home_followup_context_from_conversation(
            normalized_text=normalized_text,
            source=source,
            session_id=session_id,
            household_settings=household_settings,
        )
        if active_context is None:
            return None

    home_followup = _refine_home_assistant_followup(
        normalized_text=normalized_text,
        active_context=active_context,
        household_settings=household_settings,
    )
    if home_followup is not None:
        return home_followup

    action = _resolve_bare_transport_action(normalized_text)
    if action is None:
        return None

    route_target = str(active_context.get("route_target") or "").strip().lower()
    if route_target == "music":
        return RouteResponse(
            target="music",
            confidence=0.86,
            reason="Matched strong active session context",
            normalized_text=action,
        )
    if route_target == "audiobook" and action in {"pause", "resume", "stop"}:
        return RouteResponse(
            target="audiobook",
            confidence=0.86,
            reason="Matched strong active session context",
            normalized_text=f"{action} audiobook",
        )
    return None


def _refine_home_assistant_followup(
    *,
    normalized_text: str,
    active_context: dict[str, object],
    household_settings: HouseholdRuntimeSettings | None = None,
) -> RouteResponse | None:
    route_target = str(active_context.get("route_target") or "").strip().lower()
    if route_target != "home_assistant":
        return None
    context_text = str(active_context.get("context_text") or "")
    brightness_context_text = str(active_context.get("brightness_context_text") or "")
    if normalized_text in _HOME_FOLLOWUP_ACTION_PHRASES:
        normalized_followup = _normalize_home_followup_phrase(normalized_text)
        normalized_followup = _expand_home_followup_from_context(
            normalized_followup,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
        return RouteResponse(
            target="home_assistant",
            confidence=0.84,
            reason="Matched strong active session context",
            normalized_text=normalized_followup,
        )
    if _looks_like_structured_home_followup(normalized_text):
        normalized_followup = _expand_home_followup_from_context(
            normalized_text,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
        return RouteResponse(
            target="home_assistant",
            confidence=0.83,
            reason="Matched strong active session context",
            normalized_text=normalized_followup,
        )
    if normalized_text.startswith("what about ") and len(normalized_text) > len("what about "):
        normalized_followup = _expand_home_room_followup_from_context(
            normalized_text,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
        if normalized_followup is not None:
            return RouteResponse(
                target="home_assistant",
                confidence=0.82,
                reason="Matched strong active session context",
                normalized_text=normalized_followup,
            )
    if normalized_text.startswith("and ") and len(normalized_text) > len("and "):
        conjunction_followup = _normalize_home_conjunction_followup(
            normalized_text[len("and ") :].strip(),
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
        if conjunction_followup is not None:
            return RouteResponse(
                target="home_assistant",
                confidence=0.78,
                reason="Matched strong active session context",
                normalized_text=conjunction_followup,
            )
    return None


def _normalize_home_followup_phrase(normalized_text: str) -> str:
    if " back on" in normalized_text:
        return normalized_text.replace(" back on", " on")
    if " back off" in normalized_text:
        return normalized_text.replace(" back off", " off")
    return normalized_text


def _normalize_home_conjunction_followup(
    normalized_text: str,
    context_text: str,
    *,
    brightness_context_text: str = "",
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    if not normalized_text:
        return None
    if normalized_text in _HOME_FOLLOWUP_ACTION_PHRASES:
        normalized_followup = _normalize_home_followup_phrase(normalized_text)
        return _expand_home_followup_from_context(
            normalized_followup,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
    if _looks_like_structured_home_followup(normalized_text):
        return _expand_home_followup_from_context(
            normalized_text,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
    if normalized_text.startswith("what about ") and len(normalized_text) > len("what about "):
        return _expand_home_room_followup_from_context(
            normalized_text,
            context_text,
            brightness_context_text=brightness_context_text,
            household_settings=household_settings,
        )
    return None


def _expand_home_followup_from_context(
    normalized_text: str,
    context_text: str,
    *,
    brightness_context_text: str = "",
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str:
    target_text = _extract_home_context_target(context_text)
    if target_text:
        if normalized_text in {"turn it on", "turn them on"}:
            return f"turn on {target_text}"
        if normalized_text in {"turn it off", "turn them off"}:
            return f"turn off {target_text}"
        if normalized_text in {"switch it on", "switch them on"}:
            return f"switch on {target_text}"
        if normalized_text in {"switch it off", "switch them off"}:
            return f"switch off {target_text}"
        brightness_match = re.fullmatch(r"set (?:it|them) to (\d{1,3})", normalized_text)
        if brightness_match is not None and _context_uses_brightness_percent(
            brightness_context_text or context_text
        ):
            value = str(brightness_match.group(1) or "").strip()
            brightness_target = _extract_home_context_target(brightness_context_text or context_text)
            if value and brightness_target:
                return f"set {brightness_target} to {value} percent brightness"
            if value:
                return f"set {target_text} to {value} percent brightness"
    room_followup = _expand_home_room_followup_from_context(
        normalized_text,
        context_text,
        brightness_context_text=brightness_context_text,
        household_settings=household_settings,
    )
    if room_followup is not None:
        return room_followup
    return normalized_text


def _extract_home_context_target(context_text: str) -> str:
    text = context_text.strip()
    onoff_match = re.fullmatch(r"(?:turn|switch) (?:on|off) (.+)", text)
    if onoff_match is not None:
        target_text = str(onoff_match.group(1) or "").strip()
        if target_text:
            return target_text
    brightness_match = re.fullmatch(r"set (.+) to \d{1,3} percent brightness", text)
    if brightness_match is not None:
        target_text = str(brightness_match.group(1) or "").strip()
        if target_text:
            return target_text
    return ""


def _context_uses_brightness_percent(context_text: str) -> bool:
    return re.fullmatch(r"set .+ to \d{1,3} percent brightness", context_text.strip()) is not None


def _expand_home_room_followup_from_context(
    normalized_text: str,
    context_text: str,
    *,
    brightness_context_text: str = "",
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    room_match = re.fullmatch(r"what about (?:the )?(.+)", normalized_text)
    if room_match is None:
        return None
    new_room = canonical_room_name(
        str(room_match.group(1) or "").strip(),
        household_settings,
    )
    if not new_room:
        return None

    brightness_context = re.fullmatch(
        r"set (?:the )?(.+?) lights to (\d{1,3}) percent brightness",
        (brightness_context_text or context_text).strip(),
    )
    if brightness_context is not None:
        percent = str(brightness_context.group(2) or "").strip()
        if percent:
            return f"set the {new_room} lights to {percent} percent brightness"

    turn_context = re.fullmatch(r"(turn|switch) (on|off) (?:the )?(.+?) lights", context_text.strip())
    if turn_context is not None:
        verb = str(turn_context.group(1) or "").strip()
        state = str(turn_context.group(2) or "").strip()
        if verb and state:
            return f"{verb} {state} the {new_room} lights"

    return None


def _looks_like_structured_home_followup(normalized_text: str) -> bool:
    if normalized_text in {"open it", "open them", "close it", "close them"}:
        return True
    if any(normalized_text.startswith(prefix) for prefix in _HOME_FOLLOWUP_ACTION_PREFIXES):
        return True
    return re.fullmatch(
        r"(set|make) (it|them) (?:to )?[a-z0-9][a-z0-9 %.-]{0,40}",
        normalized_text,
    ) is not None


def _recover_home_followup_context_from_conversation(
    *,
    normalized_text: str,
    source: str,
    session_id: str | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> dict[str, object] | None:
    if not _looks_like_home_context_dependent_followup(normalized_text):
        return None
    conversation = get_conversation(source, session_id)
    if not isinstance(conversation, dict):
        return None
    history = conversation.get("history")
    if not isinstance(history, list):
        return None

    for item in reversed(history[:-1]):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        text = canonicalize_home_command(
            str(item.get("text") or "").strip(),
            household_settings=household_settings,
        )
        if not text:
            continue
        if not _looks_like_home_anchor_text(text):
            continue
        return {
            "route_target": "home_assistant",
            "anchor_strength": "strong",
            "context_text": text,
            "brightness_context_text": text if _context_uses_brightness_percent(text) else "",
        }
    return None


def _looks_like_home_context_dependent_followup(normalized_text: str) -> bool:
    if normalized_text in _HOME_FOLLOWUP_ACTION_PHRASES:
        return True
    if _looks_like_structured_home_followup(normalized_text):
        return True
    if normalized_text.startswith("what about ") and len(normalized_text) > len("what about "):
        return True
    if normalized_text.startswith("and ") and len(normalized_text) > len("and "):
        remainder = normalized_text[len("and ") :].strip()
        if remainder in _HOME_FOLLOWUP_ACTION_PHRASES:
            return True
        if _looks_like_structured_home_followup(remainder):
            return True
        if remainder.startswith("what about ") and len(remainder) > len("what about "):
            return True
    return False


def _looks_like_home_anchor_text(text: str) -> bool:
    if has_home_keyword(text)[0]:
        return True
    return bool(re.search(r"\b(light|lights|lamp|thermostat|temperature|fan|lock|door|blinds)\b", text))


def _safe_longform_state(source: str) -> dict[str, object] | None:
    try:
        return fetch_satellite_audiobook_session(source)
    except Exception:
        return None


def _safe_now_playing(source: str) -> dict[str, object] | None:
    try:
        return fetch_satellite_music_session(source)
    except Exception:
        return None


def _safe_reply_audio_state(source: str) -> dict[str, object] | None:
    try:
        return fetch_satellite_reply_audio_session(source)
    except Exception:
        return None


def _safe_playback_authority(source: str) -> dict[str, object] | None:
    try:
        return fetch_satellite_playback_authority(source)
    except Exception:
        return None


def _authority_transport_matches(
    action: str,
    authority_state: dict[str, object],
    *,
    backend_type: str | None = None,
    media_kind: str | None = None,
) -> bool:
    sessions = authority_state.get("active_sessions")
    if not isinstance(sessions, list):
        return False
    for session in sessions:
        if not isinstance(session, dict):
            continue
        current_backend = str(session.get("backend_type", "")).strip().lower()
        current_media_kind = str(session.get("media_kind", "")).strip().lower()
        if backend_type is not None and current_backend != backend_type:
            continue
        if media_kind is not None and current_media_kind != media_kind:
            continue
        current_state = str(session.get("state", "")).strip().lower()
        if action in {"pause", "stop"} and current_state in {"playing", "paused", "starting", "stopping"}:
            return True
        if action == "resume" and bool(session.get("resumable")):
            return True
        if action in {"next", "previous"} and current_media_kind == "music":
            return current_state in {"playing", "paused", "starting", "stopping"}
    return False


def _longform_transport_matches(action: str, state: dict[str, object] | None) -> bool:
    if not isinstance(state, dict):
        return False
    current_state = str(state.get("state", "")).strip().lower()
    playing = bool(state.get("playing"))
    if action in {"pause", "stop"}:
        return current_state in {"playing", "paused", "buffering"} or playing
    if action == "resume":
        return current_state in {"paused", "buffering"} or playing
    return False


def _music_transport_matches(action: str, state: dict[str, object] | None) -> bool:
    if not isinstance(state, dict):
        return False
    current_state = str(state.get("state", "")).strip().lower()
    playing = bool(state.get("playing"))
    if action in {"pause", "stop"}:
        return current_state in {"playing", "paused", "buffering"} or playing
    if action == "resume":
        return current_state in {"paused", "buffering"} or playing
    if action in {"next", "previous"}:
        return current_state in {"playing", "paused", "buffering"} or playing
    return False


def _reply_audio_transport_matches(action: str, state: dict[str, object] | None) -> bool:
    if action not in {"pause", "stop"}:
        return False
    if not isinstance(state, dict):
        return False
    return bool(state.get("playing"))
