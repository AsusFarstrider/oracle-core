from __future__ import annotations

from typing import Any

from oracle_app import state
from oracle_app.calendar import execute_calendar_query, parse_calendar_query
from oracle_app.calendar_runtime import CanonicalCalendarExecution
from oracle_app.calendar_write import build_or_continue_event_draft, commit_calendar_event
from oracle_app.config import get_calendar_settings
from oracle_app.schemas import DispatchPlan


class CalendarHandler:
    target = "calendar"

    def __init__(
        self,
        canonical_execution: CanonicalCalendarExecution | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.canonical_execution = canonical_execution
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        action = str(dispatch.payload.get("action") or "").strip()
        if action == "commit_event":
            try:
                event_draft = dict(dispatch.payload.get("event_draft") or {})
                committed = (
                    self.canonical_execution.commit_event(event_draft)
                    if self.canonical_execution is not None
                    else _calendar_write_unconfigured()
                    if self.canonical_authority
                    else commit_calendar_event(event_draft, settings=get_calendar_settings())
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
        settings = None if self.canonical_authority else get_calendar_settings()
        timezone_name = (
            self.canonical_execution.settings.timezone
            if self.canonical_execution is not None
            else "UTC"
            if self.canonical_authority
            else str(settings["timezone"])
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

        query = parse_calendar_query(normalized, timezone_name=timezone_name)
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
                if self.canonical_authority
                else execute_calendar_query(query)
            )
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "calendar_failed",
                "error": "calendar_query_failed",
                "detail": str(exc),
            }
            return dispatch

        dispatch.status = "executed"
        dispatch.result = result
        return dispatch


def _calendar_write_unconfigured():
    raise RuntimeError("calendar_write_unconfigured")


def _calendar_read_unconfigured():
    raise RuntimeError("Calendar feed is not configured")
