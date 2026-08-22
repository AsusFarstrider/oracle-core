from __future__ import annotations

from dataclasses import dataclass

from .schemas import DispatchPlan


class MediaExecutionContextError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MediaExecutionContext:
    request_source_id: str | None
    session_id: str | None
    playback_target_source_id: str | None
    target_resolution: str
    defer_audible_start: bool

    @classmethod
    def from_dispatch(
        cls,
        dispatch: DispatchPlan,
        *,
        canonical_playback_target: bool,
    ) -> MediaExecutionContext:
        payload = dispatch.payload
        request_source_id = _optional_text(payload.get("source"))
        session_id = _optional_text(payload.get("session_id"))
        if not canonical_playback_target:
            raise MediaExecutionContextError("playback_target_required")

        error = _optional_text(payload.get("playback_target_error"))
        if error is not None:
            raise MediaExecutionContextError(error)
        target = _optional_text(payload.get("playback_target_source_id"))
        resolution = _optional_text(payload.get("playback_target_resolution"))
        if target is None or resolution not in {
            "explicit",
            "authenticated_request_source",
        }:
            raise MediaExecutionContextError("playback_target_required")
        return cls(
            request_source_id=request_source_id,
            session_id=session_id,
            playback_target_source_id=target,
            target_resolution=resolution,
            defer_audible_start=resolution == "authenticated_request_source",
        )


def fail_media_execution_context(
    dispatch: DispatchPlan,
    error: MediaExecutionContextError,
) -> DispatchPlan:
    dispatch.status = "failed"
    dispatch.result = {
        "action": "playback_target_resolution",
        "error": error.code,
    }
    return dispatch


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
