from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any


class ReplyAudioStateStore:
    def __init__(
        self,
        state_path: str,
        stop_path: str,
        *,
        stale_after_seconds: float = 30.0,
    ) -> None:
        self.state_path = state_path
        self.stop_path = stop_path
        self._stale_after_seconds = max(1.0, float(stale_after_seconds))
        self._lock = threading.Lock()
        self._active_session: dict[str, Any] | None = None

    def default_state(self) -> dict[str, Any]:
        return {"ok": True, "playing": False, "kind": "tts"}

    def invalid_state(self) -> dict[str, Any]:
        return {"ok": False, "playing": False, "kind": "tts", "error": "invalid_reply_audio_state"}

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", "tts")).strip() or "tts"
        normalized: dict[str, Any] = {
            "ok": bool(payload.get("ok", True)),
            "playing": bool(payload.get("playing", False)),
            "kind": kind,
        }
        for field in (
            "session_id",
            "correlation_id",
            "state",
            "reason",
            "final_state",
            "superseded_by_session_id",
            "state_source",
        ):
            value = str(payload.get(field, "")).strip()
            if value:
                normalized[field] = value
        for field in ("updated_at", "started_at"):
            value = payload.get(field)
            if isinstance(value, (int, float)):
                normalized[field] = float(value)
        stale = payload.get("stale")
        if isinstance(stale, bool):
            normalized["stale"] = stale
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            normalized["error"] = error.strip()
        return normalized

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            self._reconcile_stale_locked()
            active_session = self._active_session
        if isinstance(active_session, dict):
            return dict(active_session)

        mirror_state = self._read_mirror_state()
        return mirror_state

    def begin_session(self, *, kind: str = "tts", correlation_id: str = "") -> dict[str, Any]:
        now = time.time()
        normalized_kind = str(kind or "tts").strip() or "tts"
        normalized_correlation_id = str(correlation_id).strip() or uuid.uuid4().hex
        session_id = uuid.uuid4().hex
        replaced_session_id = ""
        with self._lock:
            self._reconcile_stale_locked()
            if isinstance(self._active_session, dict):
                replaced_session_id = str(self._active_session.get("session_id", "")).strip()
            self._active_session = {
                "ok": True,
                "playing": True,
                "kind": normalized_kind,
                "session_id": session_id,
                "correlation_id": normalized_correlation_id,
                "state": "playing",
                "started_at": now,
                "updated_at": now,
                "state_source": "authority",
            }
        payload = {
            "ok": True,
            "playing": True,
            "kind": normalized_kind,
            "session_id": session_id,
            "correlation_id": normalized_correlation_id,
            "state": "playing",
            "started_at": now,
            "updated_at": now,
            "reply_audio_registered": True,
        }
        if replaced_session_id:
            payload["replaced_session_id"] = replaced_session_id
        return payload

    def finalize_session(
        self,
        *,
        session_id: str,
        correlation_id: str = "",
        final_state: str,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id).strip()
        normalized_correlation_id = str(correlation_id).strip()
        normalized_final_state = str(final_state).strip() or "completed"
        normalized_reason = str(reason).strip()
        now = time.time()
        matched = False
        with self._lock:
            self._reconcile_stale_locked()
            active_session = self._active_session
            if self._session_matches_locked(
                active_session,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
            ):
                matched = True
                self._active_session = None
        payload = {
            "ok": True,
            "reply_audio_finalized": matched,
            "session_id": normalized_session_id,
            "correlation_id": normalized_correlation_id,
            "final_state": normalized_final_state,
            "updated_at": now,
        }
        if normalized_reason:
            payload["reason"] = normalized_reason
        return payload

    def request_stop(self) -> dict[str, Any]:
        state = self.get_state()
        if self.stop_path:
            with open(self.stop_path, "w", encoding="utf-8") as handle:
                handle.write(str(time.time()))
        return {
            "reply_audio_state": state,
            "reply_audio_stop_requested": True,
        }

    def _read_mirror_state(self) -> dict[str, Any]:
        if not self.state_path:
            return self.default_state()
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return self.default_state()
        except (OSError, json.JSONDecodeError):
            return self.invalid_state()
        if not isinstance(payload, dict):
            return self.invalid_state()
        return self.normalize(payload)

    def _reconcile_stale_locked(self) -> None:
        if not isinstance(self._active_session, dict):
            return
        updated_at = self._active_session.get("updated_at")
        try:
            updated_at_value = float(updated_at)
        except (TypeError, ValueError):
            updated_at_value = 0.0
        if (time.time() - updated_at_value) <= self._stale_after_seconds:
            return
        self._active_session = None

    def _session_matches_locked(
        self,
        active_session: dict[str, Any] | None,
        *,
        session_id: str,
        correlation_id: str,
    ) -> bool:
        if not isinstance(active_session, dict):
            return False
        active_session_id = str(active_session.get("session_id", "")).strip()
        active_correlation_id = str(active_session.get("correlation_id", "")).strip()
        if not session_id or active_session_id != session_id:
            return False
        if correlation_id and active_correlation_id and active_correlation_id != correlation_id:
            return False
        return True
