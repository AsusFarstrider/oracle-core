from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable


@dataclass
class CommandCache:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: dict[str, float] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def get(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self.entries.get(command_id)
            return deepcopy(payload) if payload is not None else None

    def store(self, command_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.entries[command_id] = deepcopy(payload)
            self.updated_at[command_id] = time.time()

    def prune(self, max_age_seconds: float = 60.0) -> None:
        with self._lock:
            self._prune_locked(max_age_seconds=max_age_seconds)

    def get_or_store(
        self,
        command_id: str,
        operation: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Return one cached result or execute one serialized mutation."""
        with self._lock:
            cached = self.entries.get(command_id)
            if cached is not None:
                return deepcopy(cached), True
            payload = operation()
            self.entries[command_id] = deepcopy(payload)
            self.updated_at[command_id] = time.time()
            self._prune_locked(max_age_seconds=60.0)
            return deepcopy(payload), False

    def _prune_locked(self, *, max_age_seconds: float) -> None:
        now = time.time()
        for command_id in list(self.entries):
            updated_at = self.updated_at.get(command_id, now)
            if now - updated_at > max_age_seconds:
                self.entries.pop(command_id, None)
                self.updated_at.pop(command_id, None)
