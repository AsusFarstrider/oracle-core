from __future__ import annotations

from oracle_app import state
from oracle_app.alerts import build_alert_response, format_duration, list_alerts
from oracle_app.calculations import build_calculation_response
from oracle_app.constants import CACHE_PATH
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from oracle_app.calendar_runtime import CanonicalCalendarExecution
from oracle_app.home_assistant_cache import refresh_home_assistant_cache
from oracle_app.session_state import (
    clear_pending_state,
    clear_session_state,
    clear_utility_context,
    get_pending_state,
    get_utility_context,
    set_pending_state,
    set_user_context,
    set_utility_context,
)
from oracle_app.temporal import TemporalQuery, build_temporal_response, parse_temporal_query
from oracle_app.system_help import classify_help_request, failure_recovery_guidance, find_capability, render_help
from oracle_app.schemas import DispatchPlan
from oracle_app.user_context import get_user_entry, resolve_effective_user
from .home_assistant import HomeAssistantHandler


class SystemHandler:
    target = "system"

    def __init__(
        self,
        household_settings: HouseholdRuntimeSettings,
        calendar_execution: CanonicalCalendarExecution | None = None,
        home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
        satellite_settings: SatelliteFleetRuntimeSettings | None = None,
    ) -> None:
        self.household_settings = household_settings
        self.calendar_execution = calendar_execution
        self.home_assistant_settings = home_assistant_settings
        self.satellite_settings = satellite_settings

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        action = dispatch.payload.get("action")
        if action == "ignore":
            dispatch.status = "executed"
            dispatch.result = {
                "action": "ignore",
                "ignored": True,
            }
            return dispatch

        if action == "repeat":
            repeated = get_utility_context(
                dispatch.payload.get("source"),
                dispatch.payload.get("session_id"),
                kind="repeat_output",
            )
            reply_text = str(((repeated or {}).get("payload") or {}).get("reply_text") or "").strip()
            dispatch.status = "executed"
            dispatch.result = {
                "action": "repeat",
                "speech": reply_text or "I don't have a recent reply to repeat in this interaction.",
                "repeated": bool(reply_text),
            }
            return dispatch

        if action == "help":
            request = classify_help_request(str(dispatch.payload.get("text") or "")) or ("general", None)
            if request[0] == "failure":
                prior = get_utility_context(
                    dispatch.payload.get("source"), dispatch.payload.get("session_id"), kind="repeat_output"
                )
                prior_payload = (prior or {}).get("payload") or {}
                guidance = failure_recovery_guidance(
                    str(prior_payload.get("action") or ""), str(prior_payload.get("error") or "")
                )
                rendered = {
                    "help_kind": "failure",
                    "topic": str(prior_payload.get("action") or ""),
                    "speech": guidance or "I don't have specific recovery guidance for the last reply. Try restating the request with a name, time, room, or other identifying detail.",
                }
            else:
                rendered = render_help(request[0], request[1], registry=registry)
            dispatch.status = "executed"
            dispatch.result = {"action": "help", **rendered}
            return dispatch

        if action == "courtesy":
            text = str(dispatch.payload.get("text") or "").casefold()
            dispatch.status = "executed"
            dispatch.result = {
                "action": "courtesy",
                "speech": "You're welcome." if "thank" in text else "Hello. How can I help?",
            }
            return dispatch

        if action == "unsupported_utility":
            text = str(dispatch.payload.get("text") or "")
            capability = next(
                (item for item in (find_capability(word) for word in ("stopwatch", "coin", "sunrise")) if item is not None and any(alias in text for alias in item.aliases)),
                None,
            )
            if capability is None and any(word in text for word in ("dice", "die", "random")):
                capability = find_capability("randomizer")
            rendered = render_help("capability", capability.key if capability is not None else None, registry=registry)
            dispatch.status = "failed"
            dispatch.result = {
                "action": "unsupported_utility",
                "error": "capability_not_supported",
                **rendered,
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
                    math_context=get_utility_context(
                        dispatch.payload.get("source"),
                        dispatch.payload.get("session_id"),
                        kind="calculation",
                    ),
                    conversion_context=get_utility_context(
                        dispatch.payload.get("source"),
                        dispatch.payload.get("session_id"),
                        kind="conversion",
                    ),
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "calculation_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            if details.get("kind") == "conversion":
                set_utility_context(
                    dispatch.payload.get("source"),
                    dispatch.payload.get("session_id"),
                    kind="conversion",
                    payload={
                        "value": str(details.get("base_value") or ""),
                        "dimension": str(details.get("dimension") or ""),
                        "source_unit": str(details.get("source_unit") or ""),
                        "target_unit": str(details.get("target_unit") or ""),
                        "display_text": str(details.get("display_text") or speech),
                    },
                )
            else:
                set_utility_context(
                    dispatch.payload.get("source"),
                    dispatch.payload.get("session_id"),
                    kind="calculation",
                    payload={
                        "value": str(details.get("exact_value") or details.get("value") or ""),
                        "display_text": str(details.get("display_text") or speech),
                    },
                )
            dispatch.status = "executed"
            dispatch.result = {
                "action": "calculation",
                "speech": speech,
                "calculation": details,
            }
            return dispatch

        if action == "alerts":
            source = dispatch.payload.get("source")
            session_id = dispatch.payload.get("session_id")
            text = str(dispatch.payload.get("text", ""))
            pending = get_pending_state(source, session_id, domain="utilities")
            if pending is not None and str(pending.get("context_kind") or "") in {"timer", "alarm", "reminder"}:
                options = [str(item) for item in pending.get("options") or []]
                clarification_kind = str(pending.get("clarification_kind") or "")
                if clarification_kind == "reminder_time" and text.strip():
                    original = str(pending.get("original_text") or "")
                    subject = str(pending.get("subject_text") or "__TIME__")
                    text = original.replace(subject, text.strip().removeprefix("at "), 1)
                    clear_pending_state(source, session_id, domain="utilities", reason="reminder_time_resolved")
                    pending = None
                if pending is None:
                    options = []
                else:
                    matches = [
                        option for option in options
                        if text.casefold() == option.casefold() or text.casefold() in option.casefold()
                    ]
                if pending is not None and len(matches) != 1:
                    dispatch.status = "pending_clarification"
                    dispatch.result = {
                        "action": "alerts",
                        "speech": str(pending.get("prompt") or "Which alert did you mean?"),
                        "alerts": dict(pending),
                    }
                    return dispatch
                if pending is not None:
                    original = str(pending.get("original_text") or "")
                    subject = str(pending.get("subject_text") or str(pending.get("context_kind") or "alert"))
                    text = original.replace(subject, matches[0], 1)
                    clear_pending_state(source, session_id, domain="utilities", reason="alert_subject_resolved")
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
                    text,
                    alert_source,
                    session_id,
                    household_settings=self.household_settings,
                    satellite_settings=self.satellite_settings,
                    timer_context=get_utility_context(source, session_id, kind="alert_subject"),
                    timer_cancel_all_confirmed=bool(dispatch.payload.get("timer_cancel_all_confirmed")),
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "alerts_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            if details.get("status") == "confirmation_required":
                stored = state.store_pending_confirmation(
                    source,
                    session_id,
                    {
                        "dispatch": {
                            "target": "system",
                            "hook": "system.alerts",
                            "payload": {
                                **dict(dispatch.payload),
                                "text": text,
                                "timer_cancel_all_confirmed": True,
                            },
                        },
                        "prompt": speech,
                        "reason": "timer_cancel_all",
                    },
                )
                dispatch.status = "pending_confirmation" if stored else "failed"
                dispatch.result = {"action": "alerts", "speech": speech, "alerts": details}
                return dispatch
            if details.get("status") == "clarification_required":
                options = [str(item) for item in details.get("options") or []]
                stored = set_pending_state(
                    source,
                    session_id,
                    pending_type="clarification",
                    domain="utilities",
                    payload={
                        "clarification_kind": str(details.get("clarification_kind") or f"{details.get('kind') or 'alert'}_subject"),
                        "context_kind": str(details.get("kind") or "alert"),
                        "prompt": speech,
                        "options": options,
                        "original_text": str(details.get("original_text") or text),
                        "subject_text": str(details.get("subject_text") or details.get("kind") or "alert"),
                    },
                )
                dispatch.status = "pending_clarification" if stored else "failed"
                dispatch.result = {"action": "alerts", "speech": speech, "alerts": details}
                return dispatch

            if details.get("kind") == "timer" and details.get("occurrence_id"):
                duration_seconds = int(details.get("duration_seconds") or 0)
                duration_unit = "hour" if duration_seconds and duration_seconds % 3600 == 0 else "minute" if duration_seconds >= 60 else "second"
                if details.get("terminal"):
                    clear_utility_context(source, session_id, kind="alert_subject", reason="timer_terminal_action")
                else:
                    set_utility_context(
                        source,
                        session_id,
                        kind="alert_subject",
                        payload={
                            "schedule_id": str(details.get("schedule_id") or ""),
                            "occurrence_id": str(details.get("occurrence_id") or ""),
                            "alert_id": str(details.get("alert_id") or ""),
                            "alert_kind": "timer",
                            "name": str(details.get("name") or ""),
                            "duration_seconds": duration_seconds,
                            "duration_unit": duration_unit,
                        },
                    )

            if details.get("kind") == "alarm" and details.get("schedule_id"):
                if details.get("terminal"):
                    clear_utility_context(source, session_id, kind="alert_subject", reason="alarm_terminal_action")
                else:
                    set_utility_context(
                        source,
                        session_id,
                        kind="alert_subject",
                        payload={
                            "schedule_id": str(details.get("schedule_id") or ""),
                            "occurrence_id": str(details.get("occurrence_id") or ""),
                            "alert_id": str(details.get("alert_id") or ""),
                            "alert_kind": "alarm",
                            "name": str(details.get("name") or ""),
                            "schedule_type": str(details.get("schedule_type") or ""),
                        },
                    )

            if details.get("kind") == "reminder" and details.get("schedule_id"):
                if details.get("terminal"):
                    clear_utility_context(source, session_id, kind="alert_subject", reason="reminder_terminal_action")
                else:
                    set_utility_context(
                        source,
                        session_id,
                        kind="alert_subject",
                        payload={
                            "schedule_id": str(details.get("schedule_id") or ""),
                            "occurrence_id": str(details.get("occurrence_id") or ""),
                            "alert_id": str(details.get("alert_id") or ""),
                            "alert_kind": "reminder",
                            "name": str(details.get("text") or ""),
                            "schedule_type": str(details.get("schedule_type") or ""),
                        },
                    )

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

        if action in {"current_time", "current_date", "current_time_date", "temporal"}:
            source = dispatch.payload.get("source")
            session_id = dispatch.payload.get("session_id")
            text = str(dispatch.payload.get("text") or "").strip()
            pending = get_pending_state(source, session_id, domain="utilities")
            if pending is not None and str(pending.get("context_kind") or "") == "temporal":
                options = [str(item) for item in pending.get("options") or []]
                matches = [
                    option for option in options
                    if text.casefold() == option.casefold()
                    or text.casefold() in option.casefold()
                ]
                selected = matches[0] if len(matches) == 1 else None
                if selected is None:
                    dispatch.status = "pending_clarification"
                    dispatch.result = {
                        "action": "temporal",
                        "speech": str(pending.get("prompt") or "Which location did you mean?"),
                        "temporal": dict(pending),
                    }
                    return dispatch
                original = str(pending.get("original_text") or "")
                subject = str(pending.get("subject_text") or "")
                text = original.replace(subject, selected.casefold(), 1)
                clear_pending_state(source, session_id, domain="utilities", reason="temporal_location_resolved")

            query = (
                TemporalQuery(str(action), text)
                if action in {"current_time", "current_date", "current_time_date"}
                else parse_temporal_query(text)
            )
            try:
                speech, details = build_temporal_response(
                    query or text,
                    household_timezone=self.household_settings.household.timezone,
                    calendar_execution=self.calendar_execution,
                    temporal_context=get_utility_context(source, session_id, kind="temporal"),
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "temporal" if action == "temporal" else action,
                    "error": "temporal_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            result_action = "temporal" if action == "temporal" else action
            if details.get("status") == "clarification_required":
                options = [str(item) for item in details.get("options") or []]
                prompt = speech
                stored = set_pending_state(
                    source,
                    session_id,
                    pending_type="clarification",
                    domain="utilities",
                    payload={
                        "clarification_kind": "timezone_location",
                        "context_kind": "temporal",
                        "prompt": prompt,
                        "options": options,
                        "original_text": text,
                        "subject_text": str(details.get("requested_location") or ""),
                    },
                )
                if stored:
                    dispatch.status = "pending_clarification"
                else:
                    dispatch.status = "failed"
                    details["status"] = "clarification_session_unavailable"
                dispatch.result = {"action": result_action, "speech": speech, "temporal": details}
                return dispatch

            if details.get("status") != "unsupported_location":
                set_utility_context(
                    source,
                    session_id,
                    kind="temporal",
                    payload={
                        "subject_type": str(details.get("kind") or action),
                        "iso_value": str(details.get("timestamp") or details.get("date") or ""),
                        "timezone": str(details.get("timezone") or self.household_settings.household.timezone),
                        "display_text": speech,
                    },
                )
            dispatch.status = "executed"
            dispatch.result = {
                "action": result_action,
                "speech": speech,
                "temporal": details,
                **({"timestamp": details["timestamp"]} if "timestamp" in details else {}),
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
