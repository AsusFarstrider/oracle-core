from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandCache:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: dict[str, float] = field(default_factory=dict)

    def get(self, command_id: str) -> dict[str, Any] | None:
        return self.entries.get(command_id)

    def store(self, command_id: str, payload: dict[str, Any]) -> None:
        self.entries[command_id] = payload
        self.updated_at[command_id] = time.time()

    def prune(self, max_age_seconds: float = 60.0) -> None:
        now = time.time()
        for command_id in list(self.entries.keys()):
            updated_at = self.updated_at.get(command_id, now)
            if now - updated_at > max_age_seconds:
                self.entries.pop(command_id, None)
                self.updated_at.pop(command_id, None)
