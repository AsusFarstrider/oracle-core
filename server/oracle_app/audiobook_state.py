from __future__ import annotations

from copy import deepcopy
from typing import Any

from .interaction_synchronization import SynchronizationBoundary


class AudiobookRuntimeState:
    """Brain-owned ephemeral audiobook playback and provider-sync state."""

    def __init__(self) -> None:
        self._synchronization = SynchronizationBoundary()
        self._playbacks: dict[str, dict[str, Any]] = {}
        self._playback_by_source: dict[str, str] = {}
        self._pending_syncs: dict[str, dict[str, Any]] = {}

    def register_playback(self, playback_id: str, payload: dict[str, Any]) -> None:
        with self._synchronization.locked():
            stored_payload = deepcopy(payload)
            source = str(stored_payload.get("source", "")).strip()
            if source:
                previous_playback_id = self._playback_by_source.get(source)
                if previous_playback_id and previous_playback_id != playback_id:
                    self._playbacks.pop(previous_playback_id, None)
                self._playback_by_source[source] = playback_id
            self._playbacks[playback_id] = stored_payload

    def get_playback(self, playback_id: str) -> dict[str, Any] | None:
        with self._synchronization.locked():
            payload = self._playbacks.get(playback_id)
            return deepcopy(payload) if payload is not None else None

    def get_playback_for_source(self, source: str | None) -> dict[str, Any] | None:
        with self._synchronization.locked():
            source_key = str(source or "").strip()
            if not source_key:
                return None
            playback_id = self._playback_by_source.get(source_key)
            if not playback_id:
                return None
            payload = self._playbacks.get(playback_id)
            return deepcopy(payload) if payload is not None else None

    def clear_playback(self, playback_id: str) -> None:
        with self._synchronization.locked():
            payload = self._playbacks.pop(playback_id, None)
            if not payload:
                return
            source = str(payload.get("source", "")).strip()
            if source and self._playback_by_source.get(source) == playback_id:
                self._playback_by_source.pop(source, None)

    def clear_all_playbacks(self) -> None:
        with self._synchronization.locked():
            self._playbacks.clear()
            self._playback_by_source.clear()

    def upsert_pending_sync(self, sync_id: str, payload: dict[str, Any]) -> None:
        if not sync_id:
            return
        with self._synchronization.locked():
            self._pending_syncs[sync_id] = deepcopy(payload)

    def get_pending_sync(self, sync_id: str) -> dict[str, Any] | None:
        with self._synchronization.locked():
            payload = self._pending_syncs.get(sync_id)
            return deepcopy(payload) if payload is not None else None

    def mark_pending_sync_status(
        self,
        sync_id: str,
        *,
        status: str,
        attempt_count: int | None = None,
        last_error: str | None = None,
        synced_at: float | None = None,
        failed_at: float | None = None,
    ) -> None:
        with self._synchronization.locked():
            payload = self._pending_syncs.get(sync_id)
            if not isinstance(payload, dict):
                return
            payload = deepcopy(payload)
            payload["status"] = str(status).strip() or payload.get("status") or "pending"
            if attempt_count is not None:
                payload["attempt_count"] = int(attempt_count)
            if last_error is not None:
                payload["last_error"] = str(last_error)
            if synced_at is not None:
                payload["synced_at"] = float(synced_at)
            if failed_at is not None:
                payload["failed_at"] = float(failed_at)
            self._pending_syncs[sync_id] = payload

    def clear_pending_sync(self, sync_id: str) -> None:
        if not sync_id:
            return
        with self._synchronization.locked():
            self._pending_syncs.pop(sync_id, None)

    def clear_all_pending_syncs(self) -> None:
        with self._synchronization.locked():
            self._pending_syncs.clear()


AUDIOBOOK_RUNTIME_STATE = AudiobookRuntimeState()
register_active_audiobook_playback = AUDIOBOOK_RUNTIME_STATE.register_playback
get_active_audiobook_playback = AUDIOBOOK_RUNTIME_STATE.get_playback
get_active_audiobook_playback_for_source = AUDIOBOOK_RUNTIME_STATE.get_playback_for_source
clear_active_audiobook_playback = AUDIOBOOK_RUNTIME_STATE.clear_playback
clear_all_active_audiobook_playbacks = AUDIOBOOK_RUNTIME_STATE.clear_all_playbacks
upsert_pending_audiobook_sync = AUDIOBOOK_RUNTIME_STATE.upsert_pending_sync
get_pending_audiobook_sync = AUDIOBOOK_RUNTIME_STATE.get_pending_sync
mark_pending_audiobook_sync_status = AUDIOBOOK_RUNTIME_STATE.mark_pending_sync_status
clear_pending_audiobook_sync = AUDIOBOOK_RUNTIME_STATE.clear_pending_sync
clear_all_pending_audiobook_syncs = AUDIOBOOK_RUNTIME_STATE.clear_all_pending_syncs
