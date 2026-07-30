from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .tracing import log_session_event

logger = logging.getLogger("oracle-brain.session")

DEFAULT_SESSION_TIMEOUT_SECONDS = 90.0
DEFAULT_PENDING_TIMEOUT_SECONDS = 30.0

_VALID_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SOURCE_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_ALLOWED_ANCHOR_STRENGTHS = {"strong", "weak"}
_ALLOWED_PENDING_TYPES = {"confirmation", "clarification"}
_ALLOWED_PENDING_DOMAINS = {"confirmation", "music", "audiobook", "home_assistant", "calendar", "ui_context"}
_FORBIDDEN_SESSION_REFERENCE_KEYS = {
    "active_sessions",
    "alerts",
    "config",
    "control_state",
    "deploy_state",
    "deployment",
    "global_alerts",
    "playback_authority",
    "playback_state",
    "service_health",
}

_SESSIONS: dict[str, dict[str, Any]] = {}
_FALLBACK_BY_SOURCE: dict[str, str] = {}
_SESSION_AUDIT: dict[str, dict[str, dict[str, str]]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_source(source: str | None) -> str | None:
    value = str(source or "").strip()
    return value or None


def normalize_client_session_id(session_id: str | None) -> str | None:
    value = str(session_id or "").strip()
    if not value:
        return None
    if _VALID_SESSION_ID_RE.fullmatch(value) is None:
        return None
    return value


def _session_key(source: str, effective_session_id: str) -> str:
    return f"{source}:{effective_session_id}"


def _record_audit(
    key: str,
    bucket: str,
    *,
    event: str,
    reason: str,
    detail: str = "",
) -> None:
    audit = _SESSION_AUDIT.setdefault(key, {})
    audit[bucket] = {
        "event": event,
        "reason": reason,
        "detail": detail,
        "at": _utc_now_iso(),
    }


def _copy_audit(key: str) -> dict[str, dict[str, str]]:
    audit = _SESSION_AUDIT.get(key) or {}
    return {name: dict(value) for name, value in audit.items() if isinstance(value, dict)}


def _coerce_timeout(value: float | int, *, default: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return default
    if timeout <= 0:
        return default
    return timeout


def _remaining_seconds(*, created_monotonic: float, timeout_seconds: float, now_monotonic: float) -> float:
    return max(0.0, timeout_seconds - max(0.0, now_monotonic - created_monotonic))


def _has_forbidden_session_reference(payload: dict[str, Any]) -> str | None:
    for key in payload:
        normalized = str(key or "").strip().lower()
        if normalized in _FORBIDDEN_SESSION_REFERENCE_KEYS:
            return normalized
    return None


def _validate_pending_state_input(
    *,
    pending_type: str,
    domain: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> str | None:
    if pending_type not in _ALLOWED_PENDING_TYPES:
        return f"unsupported_pending_type:{pending_type or '-'}"
    if domain not in _ALLOWED_PENDING_DOMAINS:
        return f"unsupported_pending_domain:{domain or '-'}"
    if pending_type == "confirmation" and domain != "confirmation":
        return "confirmation_requires_confirmation_domain"
    if pending_type == "clarification" and domain == "confirmation":
        return "clarification_cannot_use_confirmation_domain"
    if not isinstance(payload, dict) or not payload:
        return "pending_payload_required"
    forbidden_key = _has_forbidden_session_reference(payload)
    if forbidden_key is not None:
        return f"forbidden_pending_reference:{forbidden_key}"
    if _coerce_timeout(timeout_seconds, default=0.0) <= 0:
        return "pending_timeout_must_be_positive"
    return None


def _validate_active_context_input(
    *,
    route_target: str,
    dispatch_hook: str | None,
    action: str | None,
    anchor_strength: str,
    active_room_ref: str | None,
    brightness_context_text: str | None,
) -> str | None:
    if not route_target:
        return "active_context_route_target_required"
    if anchor_strength not in _ALLOWED_ANCHOR_STRENGTHS:
        return f"unsupported_anchor_strength:{anchor_strength or '-'}"
    if anchor_strength == "strong" and not (dispatch_hook or action):
        return "strong_active_context_requires_dispatch_or_action"
    if active_room_ref and route_target != "home_assistant":
        return "active_room_ref_requires_home_assistant_context"
    if brightness_context_text and route_target != "home_assistant":
        return "brightness_context_requires_home_assistant_context"
    return None


def _build_session(
    *,
    source: str,
    client_session_id: str | None,
    effective_session_id: str,
    fallback_generated: bool,
    now_monotonic: float,
) -> dict[str, Any]:
    now_wall = _utc_now_iso()
    return {
        "session_meta": {
            "source": source,
            "client_session_id": client_session_id,
            "effective_session_id": effective_session_id,
            "fallback_generated": fallback_generated,
            "created_at": now_wall,
            "refreshed_at": now_wall,
            "created_monotonic": now_monotonic,
            "refreshed_monotonic": now_monotonic,
            "session_timeout_seconds": DEFAULT_SESSION_TIMEOUT_SECONDS,
            "pending_timeout_seconds": DEFAULT_PENDING_TIMEOUT_SECONDS,
        },
        "active_context": None,
        "pending_state": None,
        "user_context": None,
    }


def _copy_session(session: dict[str, Any]) -> dict[str, Any]:
    meta = dict(session.get("session_meta") or {})
    active = session.get("active_context")
    pending = session.get("pending_state")
    user_context = session.get("user_context")
    return {
        "session_meta": meta,
        "active_context": dict(active) if isinstance(active, dict) else active,
        "pending_state": dict(pending) if isinstance(pending, dict) else pending,
        "user_context": dict(user_context) if isinstance(user_context, dict) else user_context,
    }


def _pending_expired(session: dict[str, Any], *, now_monotonic: float) -> bool:
    pending = session.get("pending_state")
    if not isinstance(pending, dict):
        return False
    created = float(pending.get("created_monotonic") or 0.0)
    timeout = float(pending.get("timeout_seconds") or DEFAULT_PENDING_TIMEOUT_SECONDS)
    return now_monotonic - created > timeout


def _session_expired(session: dict[str, Any], *, now_monotonic: float) -> bool:
    meta = session.get("session_meta") if isinstance(session, dict) else {}
    refreshed = float((meta or {}).get("refreshed_monotonic") or 0.0)
    timeout = float((meta or {}).get("session_timeout_seconds") or DEFAULT_SESSION_TIMEOUT_SECONDS)
    return now_monotonic - refreshed > timeout


def _expire_pending_state_if_needed(
    source: str,
    session_id: str,
    session: dict[str, Any],
    *,
    now_monotonic: float,
) -> bool:
    if not _pending_expired(session, now_monotonic=now_monotonic):
        return False
    pending = session.get("pending_state")
    domain = str((pending or {}).get("domain") or "").strip() if isinstance(pending, dict) else ""
    session["pending_state"] = None
    key = _session_key(source, session_id)
    active = session.get("active_context")
    if (
        domain == "ui_context"
        and isinstance(active, dict)
        and str(active.get("dispatch_hook") or "") == "ui_context.handle_pending"
    ):
        session["active_context"] = None
        _record_audit(
            key,
            "followup",
            event="active_context_cleared",
            reason="pending_timeout",
            detail="UI context expired before the follow-up arrived.",
        )
    _record_audit(
        key,
        "pending",
        event="pending_expired",
        reason="pending_timeout",
        detail=f"{domain or 'pending'} timed out before the follow-up arrived.",
    )
    log_session_event(
        "pending_expired",
        source=source,
        session_id=session_id,
        reason="pending_timeout",
        detail=domain or "pending",
    )
    return True


def _prune_expired(now_monotonic: float | None = None) -> None:
    current = time.monotonic() if now_monotonic is None else now_monotonic
    expired_keys = [key for key, session in _SESSIONS.items() if _session_expired(session, now_monotonic=current)]
    for key in expired_keys:
        session = _SESSIONS.pop(key, None)
        if not isinstance(session, dict):
            continue
        meta = session.get("session_meta") if isinstance(session, dict) else {}
        source = str((meta or {}).get("source") or "").strip()
        effective = str((meta or {}).get("effective_session_id") or "").strip()
        _record_audit(
            key,
            "session",
            event="session_expired",
            reason="session_timeout",
            detail="Session expired before the next request refreshed it.",
        )
        log_session_event(
            "session_expired",
            source=source,
            session_id=effective,
            reason="session_timeout",
            detail="expired_before_refresh",
        )
        if source and effective and _FALLBACK_BY_SOURCE.get(source) == effective:
            _FALLBACK_BY_SOURCE.pop(source, None)


def _build_fallback_session_id(source: str) -> str:
    slug = _SOURCE_SLUG_RE.sub("-", source).strip("-").lower() or "anonymous"
    return f"fallback-{slug}-{uuid.uuid4().hex[:10]}"


def resolve_request_session(
    source: str | None,
    client_session_id: str | None,
) -> dict[str, Any]:
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)

    normalized_source = _normalize_source(source) or "anonymous"
    normalized_client_session_id = normalize_client_session_id(client_session_id)
    fallback_generated = normalized_client_session_id is None

    if normalized_client_session_id is not None:
        effective_session_id = normalized_client_session_id
    else:
        existing = _FALLBACK_BY_SOURCE.get(normalized_source)
        if existing:
            existing_key = _session_key(normalized_source, existing)
            existing_session = _SESSIONS.get(existing_key)
            if existing_session is not None and not _session_expired(existing_session, now_monotonic=now_monotonic):
                effective_session_id = existing
            else:
                _FALLBACK_BY_SOURCE.pop(normalized_source, None)
                effective_session_id = _build_fallback_session_id(normalized_source)
        else:
            effective_session_id = _build_fallback_session_id(normalized_source)
        _FALLBACK_BY_SOURCE[normalized_source] = effective_session_id
        logger.info(
            "session_fallback_generated source=%s client_session_id=%s effective_session_id=%s",
            normalized_source,
            client_session_id or "-",
            effective_session_id,
        )

    key = _session_key(normalized_source, effective_session_id)
    session = _SESSIONS.get(key)
    created_new_session = False
    if session is None:
        session = _build_session(
            source=normalized_source,
            client_session_id=normalized_client_session_id,
            effective_session_id=effective_session_id,
            fallback_generated=fallback_generated,
            now_monotonic=now_monotonic,
        )
        _SESSIONS[key] = session
        created_new_session = True
        logger.info(
            "session_created source=%s effective_session_id=%s fallback_generated=%s",
            normalized_source,
            effective_session_id,
            fallback_generated,
        )
        _record_audit(
            key,
            "session",
            event="session_created",
            reason="fallback_generated" if fallback_generated else "client_session_started",
            detail="Brain created a new live session record.",
        )
        log_session_event(
            "session_created",
            source=normalized_source,
            session_id=effective_session_id,
            reason="fallback_generated" if fallback_generated else "client_session_started",
            detail="new_session",
        )
    else:
        meta = session["session_meta"]
        meta["client_session_id"] = normalized_client_session_id
        meta["fallback_generated"] = fallback_generated
        _record_audit(
            key,
            "session",
            event="session_reused",
            reason="existing_session",
            detail="Brain reused the existing live session record.",
        )

    return {
        "source": normalized_source,
        "client_session_id": normalized_client_session_id,
        "effective_session_id": effective_session_id,
        "fallback_generated": fallback_generated,
        "created_new_session": created_new_session,
    }


def refresh_session(source: str | None, session_id: str | None) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    session = _SESSIONS.get(_session_key(normalized_source, normalized_session_id))
    if session is None:
        return False
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    return True


def get_session(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return None
    session = _SESSIONS.get(_session_key(normalized_source, normalized_session_id))
    if session is None:
        return None
    if _session_expired(session, now_monotonic=now_monotonic):
        return None
    _expire_pending_state_if_needed(
        normalized_source,
        normalized_session_id,
        session,
        now_monotonic=now_monotonic,
    )
    return _copy_session(session)


def inspect_session(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        logger.info(
            "session_inspect_invalid source=%s session_id=%s",
            source or "-",
            session_id or "-",
        )
        return None
    live_session = _SESSIONS.get(_session_key(normalized_source, normalized_session_id))
    if live_session is None or _session_expired(live_session, now_monotonic=now_monotonic):
        logger.info(
            "session_inspect_miss source=%s session_id=%s",
            normalized_source,
            normalized_session_id,
        )
        return None
    pending_expired = _expire_pending_state_if_needed(
        normalized_source,
        normalized_session_id,
        live_session,
        now_monotonic=now_monotonic,
    )
    session = _copy_session(live_session)
    key = _session_key(normalized_source, normalized_session_id)
    meta = session.get("session_meta") or {}
    pending = session.get("pending_state")
    active = session.get("active_context")
    user_context = session.get("user_context")
    followup = describe_followup_resolution(normalized_source, normalized_session_id)
    session_timeout_seconds = _coerce_timeout(meta.get("session_timeout_seconds"), default=DEFAULT_SESSION_TIMEOUT_SECONDS)
    session_refreshed_monotonic = float(meta.get("refreshed_monotonic") or 0.0)
    session_remaining_seconds = _remaining_seconds(
        created_monotonic=session_refreshed_monotonic,
        timeout_seconds=session_timeout_seconds,
        now_monotonic=now_monotonic,
    )
    pending_remaining_seconds = None
    if isinstance(pending, dict):
        pending_remaining_seconds = _remaining_seconds(
            created_monotonic=float(pending.get("created_monotonic") or 0.0),
            timeout_seconds=_coerce_timeout(pending.get("timeout_seconds"), default=DEFAULT_PENDING_TIMEOUT_SECONDS),
            now_monotonic=now_monotonic,
        )
    return {
        "ok": True,
        "session_meta": meta,
        "active_context": active,
        "pending_state": pending,
        "user_context": user_context,
        "lifecycle": _copy_audit(key),
        "derived": {
            "session_active": True,
            "pending_active": pending is not None,
            "pending_expired": pending_expired,
            "anchor_strength": str((active or {}).get("anchor_strength") or "") if isinstance(active, dict) else "",
            "active_user_id": str((user_context or {}).get("active_user_id") or "") if isinstance(user_context, dict) else "",
            "follow_up_resolution_order": str(followup.get("resolution_order") or "general_routing"),
            "waiting_on_user": bool(followup.get("waiting_on_user")),
            "next_route_target": str(followup.get("route_target") or ""),
            "pending_domain": str(followup.get("pending_domain") or ""),
            "session_seconds_remaining": round(session_remaining_seconds, 3),
            "pending_seconds_remaining": round(pending_remaining_seconds, 3) if pending_remaining_seconds is not None else None,
        },
    }


def iter_pending_states(*, domain: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    items: list[tuple[str, dict[str, Any]]] = []
    for key, session in _SESSIONS.items():
        if _session_expired(session, now_monotonic=now_monotonic):
            continue
        source, _, session_id = key.partition(":")
        if not source or not session_id:
            continue
        if _expire_pending_state_if_needed(source, session_id, session, now_monotonic=now_monotonic):
            continue
        pending = session.get("pending_state")
        if not isinstance(pending, dict):
            continue
        pending_domain = str(pending.get("domain") or "")
        if domain is not None and pending_domain != domain:
            continue
        payload = pending.get("payload")
        if not isinstance(payload, dict):
            continue
        items.append((key, dict(payload)))
    return items


def describe_followup_resolution(source: str | None, session_id: str | None) -> dict[str, Any]:
    session = get_session(source, session_id)
    if session is None:
        return {
            "resolution_order": "general_routing",
            "waiting_on_user": False,
            "route_target": "",
            "pending_domain": "",
        }

    pending = session.get("pending_state")
    if isinstance(pending, dict):
        pending_domain = str(pending.get("domain") or "").strip().lower()
        route_target = "system" if pending_domain == "confirmation" else pending_domain
        return {
            "resolution_order": "pending_state",
            "waiting_on_user": True,
            "route_target": route_target,
            "pending_domain": pending_domain,
        }

    active = session.get("active_context")
    if isinstance(active, dict):
        route_target = str(active.get("route_target") or "").strip().lower()
        anchor_strength = str(active.get("anchor_strength") or "").strip().lower()
        if route_target and anchor_strength == "strong":
            return {
                "resolution_order": "active_context",
                "waiting_on_user": False,
                "route_target": route_target,
                "pending_domain": "",
            }

    return {
        "resolution_order": "general_routing",
        "waiting_on_user": False,
        "route_target": "",
        "pending_domain": "",
    }


def get_active_context(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    session = get_session(source, session_id)
    if session is None:
        return None
    active = session.get("active_context")
    return dict(active) if isinstance(active, dict) else None


def get_user_context(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    session = get_session(source, session_id)
    if session is None:
        return None
    user_context = session.get("user_context")
    return dict(user_context) if isinstance(user_context, dict) else None


def get_active_user_id(source: str | None, session_id: str | None) -> str | None:
    user_context = get_user_context(source, session_id)
    if not isinstance(user_context, dict):
        return None
    return str(user_context.get("active_user_id") or "").strip() or None


def set_user_context(
    source: str | None,
    session_id: str | None,
    *,
    user_id: str,
    resolution_source: str,
) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    normalized_user_id = str(user_id or "").strip().lower()
    normalized_resolution_source = str(resolution_source or "").strip()
    if normalized_source is None or normalized_session_id is None or not normalized_user_id:
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    if session is None:
        session = _build_session(
            source=normalized_source,
            client_session_id=normalized_session_id,
            effective_session_id=normalized_session_id,
            fallback_generated=False,
            now_monotonic=now_monotonic,
        )
        _SESSIONS[key] = session
    session["user_context"] = {
        "active_user_id": normalized_user_id,
        "resolution_source": normalized_resolution_source or "explicit_user",
        "created_at": _utc_now_iso(),
        "refreshed_at": _utc_now_iso(),
        "created_monotonic": now_monotonic,
        "refreshed_monotonic": now_monotonic,
    }
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "session",
        event="user_context_set",
        reason=normalized_resolution_source or "explicit_user",
        detail=normalized_user_id,
    )
    logger.info(
        "user_context_set source=%s session_id=%s active_user_id=%s resolution_source=%s",
        normalized_source,
        normalized_session_id,
        normalized_user_id,
        normalized_resolution_source or "explicit_user",
    )
    return True


def clear_user_context(source: str | None, session_id: str | None) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    if session is None or session.get("user_context") is None:
        return False
    session["user_context"] = None
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "session",
        event="user_context_cleared",
        reason="user_context_cleared",
        detail="Session-owned user context was cleared.",
    )
    return True


def set_pending_state(
    source: str | None,
    session_id: str | None,
    *,
    pending_type: str,
    domain: str,
    payload: dict[str, Any],
    timeout_seconds: float = DEFAULT_PENDING_TIMEOUT_SECONDS,
) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    normalized_pending_type = str(pending_type or "").strip().lower()
    normalized_domain = str(domain or "").strip().lower()
    validation_error = _validate_pending_state_input(
        pending_type=normalized_pending_type,
        domain=normalized_domain,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    if validation_error is not None:
        logger.warning(
            "pending_state_rejected source=%s session_id=%s reason=%s",
            normalized_source,
            normalized_session_id,
            validation_error,
        )
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    if session is None:
        session = _build_session(
            source=normalized_source,
            client_session_id=normalized_session_id,
            effective_session_id=normalized_session_id,
            fallback_generated=False,
            now_monotonic=now_monotonic,
        )
        _SESSIONS[key] = session
    session["pending_state"] = {
        "type": normalized_pending_type,
        "domain": normalized_domain,
        "payload": dict(payload),
        "created_at": _utc_now_iso(),
        "refreshed_at": _utc_now_iso(),
        "created_monotonic": now_monotonic,
        "refreshed_monotonic": now_monotonic,
        "timeout_seconds": _coerce_timeout(timeout_seconds, default=DEFAULT_PENDING_TIMEOUT_SECONDS),
    }
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "pending",
        event="pending_created",
        reason=f"{normalized_domain}_pending_created",
        detail="Pending state became the first follow-up resolution layer.",
    )
    logger.info(
        "pending_state_set source=%s session_id=%s domain=%s type=%s",
        normalized_source,
        normalized_session_id,
        normalized_domain,
        normalized_pending_type,
    )
    return True


def set_active_context(
    source: str | None,
    session_id: str | None,
    *,
    route_target: str,
    dispatch_hook: str | None,
    action: str | None,
    anchor_strength: str,
    context_text: str | None = None,
    active_room_ref: str | None = None,
) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    normalized_route_target = str(route_target or "").strip().lower()
    normalized_anchor_strength = str(anchor_strength or "").strip().lower()
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    previous_active = session.get("active_context") if isinstance(session, dict) else None
    brightness_context_text = _resolve_home_brightness_context_text(
        previous_active if isinstance(previous_active, dict) else None,
        route_target=normalized_route_target,
        context_text=context_text,
    )
    validation_error = _validate_active_context_input(
        route_target=normalized_route_target,
        dispatch_hook=dispatch_hook,
        action=action,
        anchor_strength=normalized_anchor_strength,
        active_room_ref=str(active_room_ref or "").strip() or None,
        brightness_context_text=brightness_context_text,
    )
    if validation_error is not None:
        logger.warning(
            "active_context_rejected source=%s session_id=%s reason=%s",
            normalized_source,
            normalized_session_id,
            validation_error,
        )
        return False
    if session is None:
        session = _build_session(
            source=normalized_source,
            client_session_id=normalized_session_id,
            effective_session_id=normalized_session_id,
            fallback_generated=False,
            now_monotonic=now_monotonic,
        )
        _SESSIONS[key] = session
    session["active_context"] = {
        "route_target": normalized_route_target,
        "dispatch_hook": dispatch_hook,
        "action": action,
        "anchor_strength": normalized_anchor_strength,
        "context_text": context_text,
        "brightness_context_text": brightness_context_text,
        "active_room_ref": str(active_room_ref or "").strip() or None,
        "created_at": _utc_now_iso(),
        "refreshed_at": _utc_now_iso(),
        "created_monotonic": now_monotonic,
        "refreshed_monotonic": now_monotonic,
    }
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "followup",
        event="active_context_bound",
        reason="active_context",
        detail=f"{normalized_route_target}:{normalized_anchor_strength}",
    )
    logger.info(
        "active_context_set source=%s session_id=%s route_target=%s anchor_strength=%s context_text=%s",
        normalized_source,
        normalized_session_id,
        normalized_route_target,
        normalized_anchor_strength,
        (context_text or "")[:120],
    )
    return True


def _resolve_home_brightness_context_text(
    previous_active: dict[str, Any] | None,
    *,
    route_target: str,
    context_text: str | None,
) -> str | None:
    if route_target != "home_assistant":
        return None
    current_text = str(context_text or "").strip()
    if _looks_like_home_brightness_context(current_text):
        return current_text
    previous_brightness = ""
    if isinstance(previous_active, dict):
        previous_brightness = str(previous_active.get("brightness_context_text") or "").strip()
        if not previous_brightness:
            previous_context = str(previous_active.get("context_text") or "").strip()
            if _looks_like_home_brightness_context(previous_context):
                previous_brightness = previous_context
    return previous_brightness or None


def _looks_like_home_brightness_context(text: str) -> bool:
    return re.fullmatch(r"set (?:the )?.+? lights to \d{1,3} percent brightness", text.strip()) is not None


def clear_active_context(source: str | None, session_id: str | None, *, reason: str = "cleared") -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    if session is None or session.get("active_context") is None:
        return False
    session["active_context"] = None
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "followup",
        event="active_context_cleared",
        reason=reason,
        detail="Active context no longer participates in follow-up routing.",
    )
    return True


def clear_session_state(source: str | None, session_id: str | None, *, reason: str = "explicit_reset") -> dict[str, bool]:
    pending_cleared = False
    active_cleared = clear_active_context(source, session_id, reason=reason)
    user_cleared = clear_user_context(source, session_id)
    for domain in ("confirmation", "music", "audiobook", "home_assistant", "calendar", "ui_context"):
        pending_cleared = clear_pending_state(source, session_id, domain=domain, reason=reason) or pending_cleared
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is not None and normalized_session_id is not None:
        key = _session_key(normalized_source, normalized_session_id)
        _record_audit(
            key,
            "session",
            event="session_reset",
            reason=reason,
            detail="Explicit reset cleared session-owned follow-up state.",
        )
        log_session_event(
            "session_reset",
            source=normalized_source,
            session_id=normalized_session_id,
            reason=reason,
            detail="pending_and_active_context_cleared",
        )
    return {
        "pending_cleared": pending_cleared,
        "active_context_cleared": active_cleared,
        "user_context_cleared": user_cleared,
    }


def get_pending_state(
    source: str | None,
    session_id: str | None,
    *,
    domain: str | None = None,
) -> dict[str, Any] | None:
    session = get_session(source, session_id)
    if session is None:
        return None
    pending = session.get("pending_state")
    if not isinstance(pending, dict):
        return None
    if domain is not None and str(pending.get("domain") or "") != domain:
        return None
    payload = pending.get("payload")
    return dict(payload) if isinstance(payload, dict) else None


def clear_pending_state(
    source: str | None,
    session_id: str | None,
    *,
    domain: str | None = None,
    reason: str = "cleared",
) -> bool:
    normalized_source = _normalize_source(source)
    normalized_session_id = normalize_client_session_id(session_id)
    if normalized_source is None or normalized_session_id is None:
        return False
    now_monotonic = time.monotonic()
    _prune_expired(now_monotonic)
    key = _session_key(normalized_source, normalized_session_id)
    session = _SESSIONS.get(key)
    if session is None:
        return False
    pending = session.get("pending_state")
    if not isinstance(pending, dict):
        return False
    if domain is not None and str(pending.get("domain") or "") != domain:
        return False
    cleared_domain = str(pending.get("domain") or "").strip()
    session["pending_state"] = None
    meta = session["session_meta"]
    meta["refreshed_monotonic"] = now_monotonic
    meta["refreshed_at"] = _utc_now_iso()
    _record_audit(
        key,
        "pending",
        event="pending_cleared",
        reason=reason,
        detail=cleared_domain or "pending",
    )
    return True


def clear_all_sessions() -> None:
    _SESSIONS.clear()
    _FALLBACK_BY_SOURCE.clear()
    _SESSION_AUDIT.clear()
