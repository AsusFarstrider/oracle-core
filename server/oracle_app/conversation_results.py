from __future__ import annotations

import base64
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .schemas import (
    CommandRequest,
    CommandResponse,
    ConversationEffects,
    ConversationResult,
    DeferredSatellitePlaybackEffect,
    FollowUpEffect,
    SatellitePlaybackEffect,
    UiPresentationEffect,
)


GENERIC_SAFETY_REPLY = "I couldn't complete that request."


class _DeferredPlayMediaArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    media_type: str = Field(..., min_length=1)
    plex_key: str = Field(..., min_length=1)
    parent_key: str | None = None
    rating_key: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_seconds: float
    backend_hint: str | None = None
    queue_id: str | None = None
    queue_position: int | None = None
    queue_count: int | None = None
    collection_title: str | None = None
    collection_type: str | None = None
    queue_tracks: list[dict[str, Any]] | None = None


class _DeferredLongformContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["audiobook"]
    backend_type: Literal["oracle_audiobook"]
    session_id: str = Field(..., min_length=1)
    resume_action: Literal["resume_longform_audio"]


class _DeferredMusicContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["music"]
    backend_type: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    resume_action: Literal["play_media"]
    resume_args: _DeferredPlayMediaArguments


_DeferredContinuation = Annotated[
    _DeferredLongformContinuation | _DeferredMusicContinuation,
    Field(discriminator="resume_action"),
]
_DEFERRED_CONTINUATION_ADAPTER = TypeAdapter(_DeferredContinuation)


def build_conversation_result(
    *,
    request: CommandRequest,
    response: CommandResponse,
    trace_id: str,
) -> ConversationResult:
    result = response.dispatch.result or {}
    status = _public_status(response)
    reply_text = str(response.reply_text or "")
    failure_code = _failure_code(result) if status == "failed" else None
    if status != "ignored" and not reply_text.strip():
        status = "failed"
        failure_code = "malformed_internal_result"
        reply_text = GENERIC_SAFETY_REPLY

    source_id = str(response.dispatch.payload.get("source") or request.source or "").strip()
    session_id = str(response.effective_session_id or response.session_id or request.session_id or "").strip()
    if not source_id or not session_id or not str(trace_id or "").strip():
        return ConversationResult(
            reply_text=GENERIC_SAFETY_REPLY,
            session_id=session_id or "unresolved-session",
            source_id=source_id or "unresolved-source",
            status="failed",
            failure_code="malformed_internal_result",
            trace_id=str(trace_id or "unresolved-trace"),
            effects=ConversationEffects(),
        )

    return ConversationResult(
        reply_text=reply_text,
        session_id=session_id,
        source_id=source_id,
        status=status,
        failure_code=failure_code,
        trace_id=trace_id,
        effects=_effects(response, result),
    )


def _public_status(response: CommandResponse) -> str:
    result = response.dispatch.result or {}
    if bool(result.get("ignored")) or str(result.get("action") or "") == "ignore":
        return "ignored"
    if response.dispatch.status in {
        "executed",
        "pending_confirmation",
        "pending_clarification",
        "failed",
    }:
        return response.dispatch.status
    return "failed"


def _failure_code(result: dict[str, Any]) -> str:
    value = str(result.get("error") or "dispatch_failed").strip().lower()
    cleaned = "".join(character for character in value if character.isalnum() or character == "_")
    return cleaned or "dispatch_failed"


def _effects(response: CommandResponse, result: dict[str, Any]) -> ConversationEffects:
    follow_up = None
    if response.dispatch.status in {"pending_confirmation", "pending_clarification"}:
        follow_up = FollowUpEffect(
            kind="confirmation" if response.dispatch.status == "pending_confirmation" else "clarification"
        )

    satellite_playback = _satellite_playback_effect(response, result)
    deferred = _deferred_effect(result)
    ui_presentation = _ui_effect(result)
    return ConversationEffects(
        follow_up=follow_up,
        satellite_playback=satellite_playback,
        deferred_satellite_playback=deferred,
        ui_presentation=ui_presentation,
    )


def _satellite_playback_effect(
    response: CommandResponse,
    result: dict[str, Any],
) -> SatellitePlaybackEffect | None:
    if response.dispatch.target not in {"music", "audiobook"}:
        return None
    action = str(result.get("action") or "").strip()
    if not action:
        return None
    if response.dispatch.status == "failed":
        disposition = "failed"
    elif action in {"play", "resume", "resume_current"}:
        disposition = "started"
    elif action == "stop":
        disposition = "stopped"
    elif action in {"pause", "next", "previous", "restart", "set_volume", "volume_up", "volume_down"}:
        disposition = "updated"
    else:
        disposition = "unchanged"
    return SatellitePlaybackEffect(
        disposition=disposition,
        target_source_id=str(response.dispatch.payload.get("playback_target_source_id") or "").strip() or None,
    )


def _deferred_effect(result: dict[str, Any]) -> DeferredSatellitePlaybackEffect | None:
    session = result.get("deferred_session")
    if not isinstance(session, dict) or not session:
        return None
    try:
        continuation = _DEFERRED_CONTINUATION_ADAPTER.validate_python(session)
    except ValidationError as exc:
        raise ValueError("Invalid internal deferred satellite playback continuation.") from exc
    serialized = json.dumps(
        continuation.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    token = base64.urlsafe_b64encode(serialized.encode("utf-8")).decode("ascii").rstrip("=")
    return DeferredSatellitePlaybackEffect(continuation_token=token)


def decode_deferred_satellite_playback(token: str) -> dict[str, Any]:
    clean = str(token or "").strip()
    padding = "=" * (-len(clean) % 4)
    try:
        encoded = base64.b64decode(clean + padding, altchars=b"-_", validate=True)
        value = json.loads(encoded.decode("utf-8"))
        continuation = _DEFERRED_CONTINUATION_ADAPTER.validate_python(value)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("Invalid deferred satellite playback continuation token.") from exc
    return continuation.model_dump(mode="json", exclude_none=True)


def _ui_effect(result: dict[str, Any]) -> UiPresentationEffect | None:
    presentation = result.get("ui_presentation")
    if isinstance(presentation, dict):
        return UiPresentationEffect(kind="dto", presentation=presentation)
    reference = str(result.get("ui_presentation_ref") or "").strip()
    if reference:
        return UiPresentationEffect(kind="reference", reference=reference)
    return None
