from __future__ import annotations

from typing import Any

from oracle_app import state
from oracle_app.lists_notes_intents import parse_lists_notes_intent
from oracle_app.lists_notes_runtime import CanonicalListsExecution, CanonicalNotesExecution, ListsNotesError
from oracle_app.provider_bridges.nextcloud_notes import NotesBridgeError
from oracle_app.provider_bridges.nextcloud_tasks import TasksBridgeError
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_pending_state, get_pending_state, set_pending_state


class _BaseListsNotesHandler:
    target = ""

    def __init__(self, execution) -> None:
        self.execution = execution

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        if self.execution is None:
            dispatch.status = "failed"
            dispatch.result = {"error": f"{self.target}_unavailable", "detail": f"{self.target.title()} are not configured."}
            return dispatch
        source = dispatch.payload.get("source")
        session_id = dispatch.payload.get("session_id")
        confirmed_arguments = dispatch.payload.get("confirmed_arguments")
        pending = get_pending_state(source, session_id, domain="lists_notes")
        if pending is not None and str(pending.get("target_domain") or "") == self.target:
            reply = str(dispatch.payload.get("text") or "").strip()
            options = [str(item) for item in pending.get("options") or []]
            matches = [item for item in options if item.casefold() == reply.casefold()]
            if len(matches) != 1:
                dispatch.status = "pending_clarification"
                dispatch.result = {"action": pending.get("operation"), "prompt": "Which one did you mean?", "options": options}
                return dispatch
            arguments = dict(pending.get("arguments") or {})
            operation = str(pending.get("operation") or "")
            code = str(pending.get("error_code") or "")
            if code.startswith("list_"):
                arguments["list_id"] = matches[0]
            elif code.startswith("note_"):
                arguments["note_id"] = matches[0]
            else:
                arguments["lookup_title" if operation == "edit" else "title"] = matches[0]
            clear_pending_state(source, session_id, domain="lists_notes", reason="lists_notes_selection_resolved")
            intent = None
        else:
            arguments = None
            operation = ""
        intent = parse_lists_notes_intent(str(dispatch.payload.get("text") or ""))
        if arguments is None and (intent is None or intent.domain != self.target or intent.operation == "unsupported"):
            dispatch.status = "failed"
            dispatch.result = {"error": f"{self.target}_operation_unsupported", "detail": "That operation is outside the bounded capability."}
            return dispatch
        if arguments is None:
            assert intent is not None
            operation = intent.operation
            arguments = dict(confirmed_arguments) if isinstance(confirmed_arguments, dict) else dict(intent.arguments)
        if operation in {"create", "create_list"} and "user_id" not in arguments:
            user_id = self.execution.settings.source_user_ids.get(str(source or "")) or self.execution.settings.default_user_id
            if user_id is not None:
                arguments["user_id"] = user_id
        try:
            result = self.execution.execute(operation, **arguments)
        except ListsNotesError as exc:
            if exc.error_code == "confirmation_required":
                confirmed = dict(arguments)
                confirmed["confirmed"] = True
                state.store_pending_confirmation(
                    dispatch.payload.get("source"), dispatch.payload.get("session_id"),
                    {"dispatch": {"target": self.target, "hook": f"{self.target}.execute", "payload": {"action": "execute", "text": dispatch.payload.get("text"), "source": source, "session_id": session_id, "confirmed_arguments": confirmed}}},
                )
                dispatch.status = "pending_confirmation"
                dispatch.result = {"action": operation, "prompt": exc.detail}
                return dispatch
            if exc.options:
                set_pending_state(
                    source,
                    session_id,
                    pending_type="clarification",
                    domain="lists_notes",
                    payload={"target_domain": self.target, "operation": operation, "arguments": arguments, "error_code": exc.error_code, "options": exc.options},
                )
            dispatch.status = "pending_clarification" if exc.options else "failed"
            dispatch.result = {"action": operation, "error": exc.error_code, "detail": exc.detail, "options": exc.options}
            return dispatch
        except (TasksBridgeError, NotesBridgeError) as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "action": operation,
                "error": exc.error_code,
                "detail": "The provider operation could not be completed.",
            }
            return dispatch
        dispatch.status = "executed" if result.get("status") == "verified_success" else "failed"
        dispatch.result = {**result, "speech": _speech(self.target, operation, result)}
        return dispatch


class ListsHandler(_BaseListsNotesHandler):
    target = "lists"

    def __init__(self, execution: CanonicalListsExecution | None) -> None:
        super().__init__(execution)


class NotesHandler(_BaseListsNotesHandler):
    target = "notes"

    def __init__(self, execution: CanonicalNotesExecution | None) -> None:
        super().__init__(execution)


def _speech(domain: str, operation: str, result: dict[str, Any]) -> str:
    if result.get("status") == "partial_failure":
        return f"I verified {result.get('changed_count', 0)} changes, but could not finish the list. Please check it before trying again."
    if result.get("status") != "verified_success":
        return "I could not verify that operation. Please check the provider before trying again."
    if operation == "read" and domain == "lists":
        items = result.get("items") or []
        return "That list is empty." if not items else ", ".join(str(item["title"]) for item in items[:10])
    if operation == "read" and domain == "notes":
        return str((result.get("note") or {}).get("content") or "That note is empty.")
    if operation == "search":
        notes = result.get("notes") or []
        return "I didn't find a matching note." if not notes else ", ".join(str(note["title"]) for note in notes)
    return "Okay."
