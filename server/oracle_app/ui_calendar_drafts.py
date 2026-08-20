from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from .interaction_synchronization import SynchronizationBoundary


class UiCalendarDraftStore:
    """UI-owned ephemeral calendar drafts scoped by client and draft ID."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self._ttl = ttl
        self._drafts: dict[str, dict[str, Any]] = {}
        self._synchronization = SynchronizationBoundary()

    def clear_for_client(self, client_id: str) -> None:
        with self._synchronization.locked():
            self._prune_locked()
            stale = [
                draft_id
                for draft_id, payload in self._drafts.items()
                if str(payload.get("client_id") or "") == client_id
            ]
            for draft_id in stale:
                self._drafts.pop(draft_id, None)

    def store(self, client_id: str, draft_id: str, payload: dict[str, Any]) -> None:
        with self._synchronization.locked():
            self._prune_locked()
            now = datetime.now(UTC)
            self._drafts[draft_id] = {
                "client_id": client_id,
                "payload": deepcopy(payload),
                "created_at": now,
                "expires_at": now + self._ttl,
            }

    def load(self, client_id: str, draft_id: str) -> dict[str, Any] | None:
        with self._synchronization.locked():
            self._prune_locked()
            stored = self._drafts.get(draft_id)
            if stored is None or str(stored.get("client_id") or "") != client_id:
                return None
            return deepcopy(stored.get("payload") or {})

    def clear(self, client_id: str, draft_id: str) -> bool:
        with self._synchronization.locked():
            self._prune_locked()
            stored = self._drafts.get(draft_id)
            if stored is None or str(stored.get("client_id") or "") != client_id:
                return False
            self._drafts.pop(draft_id, None)
            return True

    def clear_all(self) -> None:
        with self._synchronization.locked():
            self._drafts.clear()

    def _prune_locked(self) -> None:
        now = datetime.now(UTC)
        expired = [
            draft_id
            for draft_id, payload in self._drafts.items()
            if not isinstance(payload.get("expires_at"), datetime)
            or payload["expires_at"] <= now
        ]
        for draft_id in expired:
            self._drafts.pop(draft_id, None)


UI_CALENDAR_DRAFTS = UiCalendarDraftStore()
clear_ui_calendar_drafts_for_client = UI_CALENDAR_DRAFTS.clear_for_client
store_ui_calendar_draft = UI_CALENDAR_DRAFTS.store
load_ui_calendar_draft = UI_CALENDAR_DRAFTS.load
clear_ui_calendar_draft = UI_CALENDAR_DRAFTS.clear
clear_all_ui_calendar_drafts = UI_CALENDAR_DRAFTS.clear_all
