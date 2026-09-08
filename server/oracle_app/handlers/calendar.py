from __future__ import annotations

from typing import Any

from oracle_app import state
from oracle_app.calendar import parse_calendar_query
from oracle_app.calendar_context import resolve_calendar_context, retain_calendar_context
from oracle_app.calendar_runtime import CanonicalCalendarExecution
from oracle_app.calendar_write import build_or_continue_event_draft
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_pending_state, get_pending_state, set_pending_state


class CalendarHandler:
    target = "calendar"

    def __init__(
        self,
        canonical_execution: CanonicalCalendarExecution | None = None,
    ) -> None:
        self.canonical_execution = canonical_execution

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        action = str(dispatch.payload.get("action") or "").strip()
        if action == "commit_event":
            try:
                event_draft = dict(dispatch.payload.get("event_draft") or {})
                committed = (
                    self.canonical_execution.commit_event(event_draft)
                    if self.canonical_execution is not None
                    else _calendar_write_unconfigured()
                )
            except Exception as exc:
                error_name = "calendar_write_failed"
                if "calendar_write_unconfigured" in str(exc):
                    error_name = "calendar_write_unavailable"
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "commit_event",
                    "error": error_name,
                    "detail": str(exc),
                }
                return dispatch
            dispatch.status = "executed"
            dispatch.result = {
                "action": "commit_event",
                "speech": f"Okay, I added {committed['event_draft']['title']}.",
                "calendar_event": committed,
            }
            return dispatch

        text = str(dispatch.payload.get("text", "")).strip()
        normalized = str(dispatch.payload.get("normalized_text", "")).strip() or text
        source = dispatch.payload.get("source")
        session_id = dispatch.payload.get("session_id")
        timezone_name = (
            self.canonical_execution.settings.timezone
            if self.canonical_execution is not None
            else "UTC"
        )

        pending = state.load_pending_calendar_write_request(source, session_id)
        if pending is not None:
            try:
                stage, payload = build_or_continue_event_draft(
                    normalized,
                    pending=pending,
                    timezone_name=timezone_name,
                )
            except Exception:
                dispatch.status = "pending_clarification"
                dispatch.result = {
                    "action": "create_event",
                    "prompt": "Let's finish the event first.",
                }
                return dispatch
            if stage == "clarification":
                state.store_pending_calendar_write_request(source, session_id, payload)
                dispatch.status = "pending_clarification"
                dispatch.result = {
                    "action": "create_event",
                    "prompt": payload["prompt"],
                    "missing_field": payload.get("missing_field"),
                    "collected": payload.get("collected"),
                }
                return dispatch
            state.clear_pending_calendar_write_request(source, session_id)
            state.store_pending_confirmation(
                source,
                session_id,
                {
                    "dispatch": {
                        "target": "calendar",
                        "hook": "calendar.commit_event",
                        "payload": {
                            "action": "commit_event",
                            "event_draft": payload["event_draft"],
                            "source": source,
                            "session_id": session_id,
                        },
                    }
                },
            )
            dispatch.status = "pending_confirmation"
            dispatch.result = {
                "action": "create_event",
                "prompt": payload["prompt"],
                "event_draft": payload["event_draft"],
            }
            return dispatch

        try:
            stage, payload = build_or_continue_event_draft(
                normalized,
                pending=None,
                timezone_name=timezone_name,
            )
        except Exception:
            stage = ""
            payload = {}
        if stage == "clarification":
            state.store_pending_calendar_write_request(source, session_id, payload)
            dispatch.status = "pending_clarification"
            dispatch.result = {
                "action": "create_event",
                "prompt": payload["prompt"],
                "missing_field": payload.get("missing_field"),
                "collected": payload.get("collected"),
            }
            return dispatch
        if stage == "confirmation":
            state.store_pending_confirmation(
                source,
                session_id,
                {
                    "dispatch": {
                        "target": "calendar",
                        "hook": "calendar.commit_event",
                        "payload": {
                            "action": "commit_event",
                            "event_draft": payload["event_draft"],
                            "source": source,
                            "session_id": session_id,
                        },
                    }
                },
            )
            dispatch.status = "pending_confirmation"
            dispatch.result = {
                "action": "create_event",
                "prompt": payload["prompt"],
                "event_draft": payload["event_draft"],
            }
            return dispatch

        person_id = (
            self.canonical_execution.settings.person_id_for_query(normalized, source_id=source)
            if self.canonical_execution is not None
            else None
        )
        calendar_id = (
            self.canonical_execution.settings.calendar_id_for_query(normalized)
            if self.canonical_execution is not None
            else None
        )
        pending_query = None
        informational_pending = get_pending_state(source, session_id, domain="informational")
        if (
            isinstance(informational_pending, dict)
            and informational_pending.get("target_domain") == "calendar"
            and informational_pending.get("clarification_kind") == "calendar_event_choice"
        ):
            options = [str(item) for item in informational_pending.get("options") or []]
            matches = [option for option in options if option.casefold() == normalized.casefold()]
            if len(matches) != 1:
                dispatch.status = "pending_clarification"
                dispatch.result = {
                    "action": "find_event",
                    "prompt": str(informational_pending.get("prompt") or "Which event did you mean?"),
                    "options": options,
                }
                return dispatch
            clear_pending_state(source, session_id, domain="informational", reason="calendar_event_selected")
            pending_query = parse_calendar_query(
                f"when is {matches[0]}",
                timezone_name=timezone_name,
                person_id=person_id,
                calendar_id=calendar_id,
            )
        context_resolution = resolve_calendar_context(
            normalized,
            source=source,
            session_id=session_id,
            timezone_name=timezone_name,
            person_id=person_id,
            calendar_id=calendar_id,
        )
        if context_resolution.result is not None:
            dispatch.status = "executed"
            dispatch.result = context_resolution.result
            return dispatch
        query = pending_query or context_resolution.query or parse_calendar_query(
            normalized,
            timezone_name=timezone_name,
            person_id=person_id,
            calendar_id=calendar_id,
        )
        if query is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "calendar_failed",
                "error": "calendar_unrecognized",
                "detail": "Oracle could not parse that calendar request.",
            }
            return dispatch

        try:
            result = (
                self.canonical_execution.execute(query)
                if self.canonical_execution is not None
                else _calendar_read_unconfigured()
            )
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "calendar_failed",
                "error": "calendar_query_failed",
                "detail": str(exc),
            }
            return dispatch

        if result.get("ambiguous"):
            options = list(dict.fromkeys(
                str(item.get("summary") or "").strip()
                for item in result.get("events") or []
                if isinstance(item, dict) and str(item.get("summary") or "").strip()
            ))[:5]
            if len(options) >= 2:
                prompt = f"I found {', '.join(options)}. Which event did you mean?"
                set_pending_state(
                    source,
                    session_id,
                    pending_type="clarification",
                    domain="informational",
                    payload={
                        "target_domain": "calendar",
                        "clarification_kind": "calendar_event_choice",
                        "prompt": prompt,
                        "options": options,
                        "original_text": normalized,
                        "subject_id": "calendar_event_choice",
                    },
                )
                dispatch.status = "pending_clarification"
                dispatch.result = {"action": "find_event", "prompt": prompt, "options": options}
                return dispatch

        dispatch.status = "executed"
        dispatch.result = result
        if str(result.get("action") or "") != "calendar_unsupported_mutation":
            retain_calendar_context(result, source=source, session_id=session_id)
        return dispatch


def _calendar_write_unconfigured():
    raise RuntimeError("calendar_write_unconfigured")


def _calendar_read_unconfigured():
    raise RuntimeError("Calendar feed is not configured")
