from __future__ import annotations

from oracle_app.lists_notes_intents import parse_lists_notes_intent
from oracle_app.session_state import get_pending_state
from .base import CapabilityDecision


class ListsNotesCapability:
    name = "lists_notes"
    priority = 78

    def __init__(self, *, lists_enabled: bool, notes_enabled: bool) -> None:
        self.lists_enabled = lists_enabled
        self.notes_enabled = notes_enabled

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        pending = get_pending_state(source, session_id, domain="lists_notes")
        if pending is not None and normalized_text:
            target = str(pending.get("target_domain") or "")
            if target in {"lists", "notes"}:
                return CapabilityDecision(target, 0.98, "Matched pending Lists/Notes clarification", normalized_text)
        intent = parse_lists_notes_intent(normalized_text)
        if intent is None or intent.operation == "unsupported":
            return None
        if intent.domain == "lists" and not self.lists_enabled:
            return None
        if intent.domain == "notes" and not self.notes_enabled:
            return None
        return CapabilityDecision(intent.domain, 0.92, f"Matched deterministic {intent.domain} request", normalized_text)
