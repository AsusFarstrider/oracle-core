from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from .tracing import log_pending_event
from . import session_state

ACTIVE_AUDIOBOOK_PLAYBACKS: dict[str, dict[str, Any]] = {}
ACTIVE_AUDIOBOOK_BY_SOURCE: dict[str, str] = {}
PENDING_AUDIOBOOK_SYNCS: dict[str, dict[str, Any]] = {}
UI_CALENDAR_DRAFTS: dict[str, dict[str, Any]] = {}
_UI_CALENDAR_DRAFT_TTL = timedelta(minutes=15)
UI_CONTEXT_PENDING_TIMEOUT_SECONDS = 180.0


def build_scoped_state_key(source: str | None, session_id: str | None) -> str | None:
    source_key = str(source).strip() if source else ""
    session_key = str(session_id).strip() if session_id else ""
    if not source_key or not session_key:
        return None
    return f"{source_key}:{session_key}"


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


def load_pending_confirmation(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="confirmation")
    return deepcopy(payload) if payload is not None else None


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


def load_pending_home_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="home_assistant")
    return deepcopy(payload) if payload is not None else None


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


def load_pending_calendar_write_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="calendar")
    return deepcopy(payload) if payload is not None else None


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


def _prune_ui_calendar_drafts() -> None:
    now = datetime.now(UTC)
    expired = [
        draft_id
        for draft_id, payload in UI_CALENDAR_DRAFTS.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload["expires_at"] <= now
    ]
    for draft_id in expired:
        UI_CALENDAR_DRAFTS.pop(draft_id, None)


def clear_ui_calendar_drafts_for_client(client_id: str) -> None:
    _prune_ui_calendar_drafts()
    stale = [draft_id for draft_id, payload in UI_CALENDAR_DRAFTS.items() if str(payload.get("client_id") or "") == client_id]
    for draft_id in stale:
        UI_CALENDAR_DRAFTS.pop(draft_id, None)


def store_ui_calendar_draft(client_id: str, draft_id: str, payload: dict[str, Any]) -> None:
    _prune_ui_calendar_drafts()
    UI_CALENDAR_DRAFTS[draft_id] = {
        "client_id": client_id,
        "payload": deepcopy(payload),
        "created_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + _UI_CALENDAR_DRAFT_TTL,
    }


def load_ui_calendar_draft(client_id: str, draft_id: str) -> dict[str, Any] | None:
    _prune_ui_calendar_drafts()
    stored = UI_CALENDAR_DRAFTS.get(draft_id)
    if stored is None:
        return None
    if str(stored.get("client_id") or "") != client_id:
        return None
    return deepcopy(stored.get("payload") or {})


def clear_ui_calendar_draft(client_id: str, draft_id: str) -> bool:
    _prune_ui_calendar_drafts()
    stored = UI_CALENDAR_DRAFTS.get(draft_id)
    if stored is None or str(stored.get("client_id") or "") != client_id:
        return False
    UI_CALENDAR_DRAFTS.pop(draft_id, None)
    return True


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


def load_pending_music_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="music")
    return deepcopy(payload) if payload is not None else None


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


def load_pending_audiobook_request(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="audiobook")
    return deepcopy(payload) if payload is not None else None


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


def load_pending_ui_context(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = build_scoped_state_key(source, session_id)
    if key is None:
        return None
    payload = session_state.get_pending_state(source, session_id, domain="ui_context")
    return deepcopy(payload) if payload is not None else None


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


def register_active_audiobook_playback(playback_id: str, payload: dict[str, Any]) -> None:
    stored_payload = deepcopy(payload)
    source = str(stored_payload.get("source", "")).strip()
    if source:
        previous_playback_id = ACTIVE_AUDIOBOOK_BY_SOURCE.get(source)
        if previous_playback_id and previous_playback_id != playback_id:
            ACTIVE_AUDIOBOOK_PLAYBACKS.pop(previous_playback_id, None)
        ACTIVE_AUDIOBOOK_BY_SOURCE[source] = playback_id
    ACTIVE_AUDIOBOOK_PLAYBACKS[playback_id] = stored_payload


def get_active_audiobook_playback(playback_id: str) -> dict[str, Any] | None:
    payload = ACTIVE_AUDIOBOOK_PLAYBACKS.get(playback_id)
    return deepcopy(payload) if payload is not None else None


def get_active_audiobook_playback_for_source(source: str | None) -> dict[str, Any] | None:
    source_key = str(source or "").strip()
    if not source_key:
        return None
    playback_id = ACTIVE_AUDIOBOOK_BY_SOURCE.get(source_key)
    if not playback_id:
        return None
    return get_active_audiobook_playback(playback_id)


def clear_active_audiobook_playback(playback_id: str) -> None:
    payload = ACTIVE_AUDIOBOOK_PLAYBACKS.pop(playback_id, None)
    if not payload:
        return
    source = str(payload.get("source", "")).strip()
    if source and ACTIVE_AUDIOBOOK_BY_SOURCE.get(source) == playback_id:
        ACTIVE_AUDIOBOOK_BY_SOURCE.pop(source, None)


def clear_all_active_audiobook_playbacks() -> None:
    ACTIVE_AUDIOBOOK_PLAYBACKS.clear()
    ACTIVE_AUDIOBOOK_BY_SOURCE.clear()


def upsert_pending_audiobook_sync(sync_id: str, payload: dict[str, Any]) -> None:
    if not sync_id:
        return
    PENDING_AUDIOBOOK_SYNCS[sync_id] = deepcopy(payload)


def get_pending_audiobook_sync(sync_id: str) -> dict[str, Any] | None:
    payload = PENDING_AUDIOBOOK_SYNCS.get(sync_id)
    return deepcopy(payload) if payload is not None else None


def mark_pending_audiobook_sync_status(
    sync_id: str,
    *,
    status: str,
    attempt_count: int | None = None,
    last_error: str | None = None,
    synced_at: float | None = None,
    failed_at: float | None = None,
) -> None:
    payload = PENDING_AUDIOBOOK_SYNCS.get(sync_id)
    if not isinstance(payload, dict):
        return
    payload["status"] = str(status).strip() or payload.get("status") or "pending"
    if attempt_count is not None:
        payload["attempt_count"] = int(attempt_count)
    if last_error is not None:
        payload["last_error"] = str(last_error)
    if synced_at is not None:
        payload["synced_at"] = float(synced_at)
    if failed_at is not None:
        payload["failed_at"] = float(failed_at)
    PENDING_AUDIOBOOK_SYNCS[sync_id] = deepcopy(payload)


def clear_pending_audiobook_sync(sync_id: str) -> None:
    if not sync_id:
        return
    PENDING_AUDIOBOOK_SYNCS.pop(sync_id, None)
