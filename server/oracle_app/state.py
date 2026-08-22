from __future__ import annotations

from copy import deepcopy
from typing import Any

from .tracing import log_pending_event
from . import session_state
from .interaction_synchronization import synchronized_interaction

UI_CONTEXT_PENDING_TIMEOUT_SECONDS = 180.0


def build_scoped_state_key(source: str | None, session_id: str | None) -> str | None:
    source_key = str(source).strip() if source else ""
    session_key = str(session_id).strip() if session_id else ""
    if not source_key or not session_key:
        return None
    return f"{source_key}:{session_key}"


@synchronized_interaction
def store_pending_confirmation(source: str | None, session_id: str | None, payload: dict[str, Any]) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="confirmation",
        domain="confirmation",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="system",
        dispatch_hook="system.confirm_pending",
        action="confirm_pending",
        anchor_strength="strong",
    )
    log_pending_event(
        "pending_created",
        pending_kind="confirmation",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_confirmation(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="confirmation")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_confirmation(source: str | None, session_id: str | None) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    cleared = session_state.clear_pending_state(source, session_id, domain="confirmation")
    if cleared:
        log_pending_event(
            "pending_resolved",
            pending_kind="confirmation",
            source=source,
            session_id=session_id,
        )
    return cleared


@synchronized_interaction
def store_pending_home_request(source: str | None, session_id: str | None, payload: dict[str, Any]) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="home_assistant",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="home_assistant",
        dispatch_hook="home_assistant.execute",
        action="execute",
        anchor_strength="strong",
        context_text=str(stored_payload.get("base_text") or ""),
        active_room_ref=str(stored_payload.get("resolved_room") or "").strip() or None,
    )
    log_pending_event(
        "pending_created",
        pending_kind="home_assistant",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_home_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="home_assistant")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_home_request(source: str | None, session_id: str | None) -> None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return
    cleared = session_state.clear_pending_state(source, session_id, domain="home_assistant")
    if cleared:
        log_pending_event(
            "pending_resolved",
            pending_kind="home_assistant",
            source=source,
            session_id=session_id,
        )


@synchronized_interaction
def store_pending_calendar_write_request(source: str | None, session_id: str | None, payload: dict[str, Any]) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="calendar",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="calendar",
        dispatch_hook="calendar.execute",
        action="create_event",
        anchor_strength="strong",
        context_text=str(stored_payload.get("source_text") or ""),
    )
    log_pending_event(
        "pending_created",
        pending_kind="calendar",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_calendar_write_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="calendar")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_calendar_write_request(source: str | None, session_id: str | None) -> None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return
    cleared = session_state.clear_pending_state(source, session_id, domain="calendar")
    if cleared:
        log_pending_event(
            "pending_resolved",
            pending_kind="calendar",
            source=source,
            session_id=session_id,
        )


@synchronized_interaction
def store_pending_music_request(source: str | None, session_id: str | None, payload: dict[str, Any]) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="music",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="music",
        dispatch_hook="music.execute",
        action="play",
        anchor_strength="strong",
    )
    log_pending_event(
        "pending_created",
        pending_kind="music",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_music_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="music")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_music_request(source: str | None, session_id: str | None) -> None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return
    cleared = session_state.clear_pending_state(source, session_id, domain="music")
    if cleared:
        log_pending_event(
            "pending_resolved",
            pending_kind="music",
            source=source,
            session_id=session_id,
        )


@synchronized_interaction
def store_pending_audiobook_request(source: str | None, session_id: str | None, payload: dict[str, Any]) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="audiobook",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="audiobook",
        dispatch_hook="audiobook.execute",
        action="play",
        anchor_strength="strong",
    )
    log_pending_event(
        "pending_created",
        pending_kind="audiobook",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def narrow_pending_audiobook_request(
    source: str | None,
    session_id: str | None,
    payload: dict[str, Any],
) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="audiobook",
        payload=stored_payload,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="audiobook",
        dispatch_hook="audiobook.execute",
        action="play",
        anchor_strength="strong",
    )
    log_pending_event(
        "pending_narrowed",
        pending_kind="audiobook",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_audiobook_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="audiobook")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_audiobook_request(source: str | None, session_id: str | None) -> None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return
    cleared = session_state.clear_pending_state(source, session_id, domain="audiobook")
    if cleared:
        log_pending_event(
            "pending_resolved",
            pending_kind="audiobook",
            source=source,
            session_id=session_id,
        )


@synchronized_interaction
def store_pending_ui_context(
    source: str | None,
    session_id: str | None,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = UI_CONTEXT_PENDING_TIMEOUT_SECONDS,
) -> bool:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return False
    stored_payload = deepcopy(payload)
    if not session_state.set_pending_state(
        source,
        session_id,
        pending_type="clarification",
        domain="ui_context",
        payload=stored_payload,
        timeout_seconds=timeout_seconds,
    ):
        return False
    session_state.set_active_context(
        source,
        session_id,
        route_target="system",
        dispatch_hook="ui_context.handle_pending",
        action=str(stored_payload.get("action") or "ui_context"),
        anchor_strength="strong",
        context_text=str(stored_payload.get("prompt") or ""),
    )
    log_pending_event(
        "pending_created",
        pending_kind="ui_context",
        source=source,
        session_id=session_id,
    )
    return True


@synchronized_interaction
def load_pending_ui_context(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="ui_context")
    return deepcopy(payload) if payload is not None else None


@synchronized_interaction
def clear_pending_ui_context(source: str | None, session_id: str | None) -> None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return
    cleared = session_state.clear_pending_state(source, session_id, domain="ui_context")
    if cleared:
        session_state.clear_active_context(source, session_id, reason="ui_context_resolved")
        log_pending_event(
            "pending_resolved",
            pending_kind="ui_context",
            source=source,
            session_id=session_id,
        )
