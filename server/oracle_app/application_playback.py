from __future__ import annotations

from fastapi import HTTPException

from .application_runtime import app, brain_application_composition
from .music_runtime.control import ControlPlaneError, build_control_plane_failure
from .schemas import VoiceDeferredResumeRequest


def _canonical_playback_execution(source_id: str):
    composition = brain_application_composition(app)
    for execution in (composition.music_execution, composition.audiobook_execution):
        if execution is not None and execution.settings.playback_target(source_id) is not None:
            return execution
    return None


def deferred_resume(payload: VoiceDeferredResumeRequest) -> dict[str, object]:
    source = str(payload.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    deferred_session = payload.deferred_session if isinstance(payload.deferred_session, dict) else {}
    resume_action = str(deferred_session.get("resume_action") or "").strip()
    if resume_action not in {"resume_longform_audio", "play_media"}:
        raise HTTPException(status_code=400, detail="Unsupported deferred resume action")
    resume_args = deferred_session.get("resume_args")
    if resume_action == "resume_longform_audio":
        command_args = None
    elif isinstance(resume_args, dict):
        command_args = resume_args
    else:
        raise HTTPException(status_code=400, detail="Deferred music resume requires resume_args")

    playback_execution = _canonical_playback_execution(source)
    if playback_execution is None:
        raise HTTPException(status_code=400, detail="Source is not an admitted canonical audio target")
    try:
        satellite = playback_execution.execute_satellite_command(source, resume_action, command_args)
    except ControlPlaneError as exc:
        return {
            "ok": False,
            "source": source,
            "deferred_session": {
                "kind": str(deferred_session.get("kind") or ""),
                "backend_type": str(deferred_session.get("backend_type") or ""),
                "session_id": str(deferred_session.get("session_id") or ""),
                "resume_action": resume_action,
            },
            "result": build_control_plane_failure(action=resume_action, exc=exc),
        }

    return {
        "ok": True,
        "source": source,
        "deferred_session": {
            "kind": str(deferred_session.get("kind") or ""),
            "backend_type": str(deferred_session.get("backend_type") or ""),
            "session_id": str(deferred_session.get("session_id") or ""),
            "resume_action": resume_action,
        },
        "satellite": satellite,
    }
