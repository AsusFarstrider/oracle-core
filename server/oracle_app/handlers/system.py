from __future__ import annotations

from datetime import datetime

from oracle_app import state
from oracle_app.alerts import build_alert_response, format_duration, list_alerts
from oracle_app.calculations import build_calculation_response
from oracle_app.constants import CACHE_PATH
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.calendar_runtime import CanonicalCalendarExecution
from oracle_app.home_assistant_cache import refresh_home_assistant_cache
from oracle_app.session_state import clear_session_state, set_user_context
from oracle_app.schemas import DispatchPlan
from oracle_app.user_context import get_user_entry, resolve_effective_user
from .home_assistant import HomeAssistantHandler


class SystemHandler:
    target = "system"

    def __init__(
        self,
        household_settings: HouseholdRuntimeSettings | None = None,
        calendar_execution: CanonicalCalendarExecution | None = None,
        home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    ) -> None:
        self.household_settings = household_settings
        self.calendar_execution = calendar_execution
        self.home_assistant_settings = home_assistant_settings

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        action = dispatch.payload.get("action")
        if action == "ignore":
            dispatch.status = "executed"
            dispatch.result = {
                "action": "ignore",
                "ignored": True,
            }
            return dispatch

        if action == "confirm_pending":
            pending = state.load_pending_confirmation(
                dispatch.payload.get("source"),
                dispatch.payload.get("session_id"),
            )
            if pending is None:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "no_pending_confirmation",
                    "detail": "There is no pending action to confirm",
                }
                return dispatch

            state.clear_pending_confirmation(
                dispatch.payload.get("source"),
                dispatch.payload.get("session_id"),
            )
            pending_dispatch = DispatchPlan(
                target=str(pending["dispatch"]["target"]),
                hook=str(pending["dispatch"]["hook"]),
                payload=dict(pending["dispatch"]["payload"]),
                status="planned",
            )
            if pending_dispatch.target == "home_assistant":
                home_handler = registry.get("home_assistant")
                if not isinstance(home_handler, HomeAssistantHandler):
                    dispatch.status = "failed"
                    dispatch.result = {
                        "error": "home_assistant_handler_unavailable",
                        "detail": "The Home Assistant confirmation handler is unavailable.",
                    }
                    return dispatch
                confirmed = home_handler.handle_confirmed(pending_dispatch)
            else:
                confirmed = registry.execute(pending_dispatch)

            dispatch.status = confirmed.status
            dispatch.result = {
                "action": "confirm_pending",
                "confirmed_dispatch": {
                    "target": confirmed.target,
                    "hook": confirmed.hook,
                    "payload": confirmed.payload,
                    "status": confirmed.status,
                    "result": confirmed.result,
                },
            }
            return dispatch

        if action == "cancel_pending":
            source = dispatch.payload.get("source")
            session_id = dispatch.payload.get("session_id")
            reset_result = clear_session_state(source, session_id, reason="explicit_cancel")
            dispatch.status = "executed"
            dispatch.result = {
                "action": "cancel_pending",
                "canceled": bool(reset_result["pending_cleared"] or reset_result["active_context_cleared"]),
                "pending_cleared": reset_result["pending_cleared"],
                "active_context_cleared": reset_result["active_context_cleared"],
                "user_context_cleared": reset_result["user_context_cleared"],
            }
            return dispatch

        if action == "switch_user":
            requested_name = str(dispatch.payload.get("requested_user_name") or dispatch.payload.get("text") or "").strip()
            resolved = resolve_effective_user(
                source=dispatch.payload.get("source"),
                session_id=dispatch.payload.get("session_id"),
                requested_user_name=requested_name,
                household_settings=self.household_settings,
            )
            if not resolved.get("ok"):
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "switch_user",
                    "error": str(resolved.get("error") or "unknown_user"),
                    "requested_user_name": requested_name,
                }
                return dispatch

            user_id = str(resolved.get("user_id") or "").strip()
            entry = get_user_entry(
                user_id,
                household_settings=self.household_settings,
            ) or {}
            set_user_context(
                dispatch.payload.get("source"),
                dispatch.payload.get("session_id"),
                user_id=user_id,
                resolution_source="explicit_switch",
            )
            dispatch.status = "executed"
            dispatch.result = {
                "action": "switch_user",
                "user_id": user_id,
                "display_name": str(entry.get("display_name") or user_id),
            }
            return dispatch

        if action == "calculation":
            try:
                speech, details = build_calculation_response(
                    str(dispatch.payload.get("text", "")),
                    calendar_execution=self.calendar_execution,
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "calculation_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "calculation",
                "speech": speech,
                "calculation": details,
            }
            return dispatch

        if action == "alerts":
            target_error = str(dispatch.payload.get("alert_delivery_target_error") or "").strip()
            if target_error:
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "alerts",
                    "error": target_error,
                    "detail": "A durable alert requires an authorized managed alert destination.",
                }
                return dispatch
            alert_source = (
                dispatch.payload.get("alert_delivery_target_source_id")
                or dispatch.payload.get("source")
            )
            try:
                speech, details = build_alert_response(
                    str(dispatch.payload.get("text", "")),
                    alert_source,
                    dispatch.payload.get("session_id"),
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "alerts_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            speech = _augment_timer_status_with_sleep_timer(
                speech,
                details,
                source=alert_source,
            )
            dispatch.status = "executed"
            dispatch.result = {
                "action": "alerts",
                "speech": speech,
                "alerts": details,
            }
            return dispatch

        if action in {"current_time", "current_date", "current_time_date"}:
            now = datetime.now().astimezone()
            time_text = now.strftime("%I:%M %p").lstrip("0")
            date_text = now.strftime("%A, %B %d, %Y").replace(" 0", " ")

            if action == "current_time":
                speech = f"It is {time_text}."
            elif action == "current_date":
                speech = f"Today is {date_text}."
            else:
                speech = f"It is {time_text} on {date_text}."

            dispatch.status = "executed"
            dispatch.result = {
                "action": action,
                "speech": speech,
                "timestamp": now.isoformat(),
            }
            return dispatch

        if action != "refresh_cache":
            dispatch.status = "failed"
            dispatch.result = {
                "error": "unknown_system_action",
                "detail": str(action),
            }
            return dispatch

        try:
            cache = refresh_home_assistant_cache(self.home_assistant_settings)
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "error": "system_action_failed",
                "detail": str(exc),
            }
            return dispatch

        dispatch.status = "executed"
        dispatch.result = {
            "action": "refresh_cache",
            "room_count": cache["room_count"],
            "entity_count": cache["entity_count"],
            "cache_path": str(CACHE_PATH),
        }
        return dispatch


def _augment_timer_status_with_sleep_timer(
    speech: str,
    details: dict[str, object],
    *,
    source: str | None,
) -> str:
    if str(details.get("kind", "")).strip() != "timer":
        return speech
    if str(details.get("operation", "")).strip() != "status":
        return speech

    sleep_timers = list_alerts(source, "sleep_timer")
    if not sleep_timers:
        return speech

    next_sleep_timer = sleep_timers[0]
    remaining_seconds = max(0.0, (next_sleep_timer.due_at - datetime.now().astimezone()).total_seconds())
    note = f"The audiobook sleep timer has {format_duration(remaining_seconds)} remaining."
    if not speech:
        return note
    return f"{speech} {note}"
