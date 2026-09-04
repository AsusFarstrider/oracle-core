from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, Request, Response

from . import alerts as alerts_module
from .application_command import _canonical_http_request_source, command_request
from .application_runtime import app, brain_application_composition
from .calendar_runtime import CalendarReadUnavailableError
from .configuration.request_source_resolution import ResolvedRequestSource
from .home_assistant_actions import (
    execute_home_assistant_ui_action,
    resolve_home_assistant_dynamic_ui_action,
)
from .network import build_ui_network_health_snapshot
from .schemas import (
    CommandRequest,
    UiActionRequest,
    UiAlarmActionRequest,
    UiContextStartRequest,
    UiReminderActionRequest,
    UiTimerActionRequest,
)
from .ui_audio import (
    build_ui_audio_snapshot,
    build_ui_audio_status_snapshot,
    resolve_ui_audio_source,
    ui_audio_search_impl,
)
from .ui_audio_control import (
    start_current_audiobook_for_user,
    set_audiobook_sleep_timer_seconds,
    ui_audio_control_impl,
    ui_audio_play_impl,
    ui_audio_sleep_timer_impl,
)
from .ui_calendar import (
    build_ui_calendar_page_snapshot as _build_ui_calendar_page_snapshot,
    build_ui_calendar_snapshot as _build_ui_calendar_snapshot,
    build_ui_calendar_unavailable_page_snapshot,
    build_ui_calendar_unavailable_snapshot,
    ui_calendar_confirm_impl as _ui_calendar_confirm_impl,
)
from .ui_context import ui_context_start_impl, ui_alarm_cancel_impl as _ui_alarm_cancel_impl
from .ui_house import (
    build_canonical_ui_home_assistant_snapshot,
    build_ui_house_snapshot,
    ui_house_camera_snapshot_impl,
)
from .ui_satellite import build_satellite_ui_config, build_satellite_ui_home_snapshot
from .timers import build_timer_state, dismiss_timer
from .alarms import build_alarm_state, manage_alarm
from .reminders import build_reminder_state, manage_reminder
from .ui_snapshot_cache import get_cached_snapshot, invalidate_cached_snapshots
from .ui_weather import build_ui_weather_snapshot as _build_ui_weather_snapshot


logger = logging.getLogger("oracle-brain.application.ui")


def _canonical_playback_execution(source_id: str):
    composition = brain_application_composition(app)
    for execution in (composition.music_execution, composition.audiobook_execution):
        if execution is not None and execution.settings.playback_target(source_id) is not None:
            return execution
    return None


def _normalize_ui_client_id(client_id: str) -> str:
    normalized = str(client_id).strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="client_id cannot be empty")
    if " " in normalized:
        raise HTTPException(status_code=400, detail="client_id must not contain spaces")
    if not normalized.replace("-", "").isalnum() or normalized.startswith("-") or normalized.endswith("-"):
        raise HTTPException(
            status_code=400,
            detail="client_id must be lowercase, hyphen-separated, and contain only letters, numbers, and hyphens",
        )
    return normalized


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _build_ui_home_weather_payload() -> dict[str, object]:
    composition = brain_application_composition(app)
    try:
        if composition.weather_execution is None:
            _canonical_weather_ui_unavailable()
        _speech, weather = composition.weather_execution.build_current_response("")
        try:
            forecast_payload = composition.weather_execution.fetch_forecast()
            forecast_periods = list(forecast_payload.get("periods") or [])
        except Exception:
            forecast_periods = []
        primary_forecast = forecast_periods[0] if forecast_periods else {}
        weather_payload = {
            "summary": str(weather.get("speech_summary") or _speech or "").strip() or str(_speech),
            "condition": str(getattr(primary_forecast, "short_forecast", "") or "").strip() or None,
            "temperature_f": weather.get("temperature_f"),
            "freshness_class": weather.get("freshness_class"),
            "humidity_pct": weather.get("humidity_pct"),
            "wind_speed_mph": weather.get("wind_speed_mph"),
            "source_name": weather.get("source_name"),
        }
    except Exception as exc:
        weather_payload = {
            "summary": "Weather is temporarily unavailable.",
            "temperature_f": None,
            "freshness_class": "unavailable",
            "detail": str(exc),
        }
    return weather_payload


def _cached_ui_home_weather_payload() -> dict[str, object]:
    return get_cached_snapshot(
        "ui_home_weather",
        ttl_seconds=60,
        builder=_build_ui_home_weather_payload,
    )


def _cached_ui_weather_snapshot() -> dict[str, object]:
    composition = brain_application_composition(app)
    if composition.weather_execution is None:
        raise RuntimeError("Weather capability is not configured")
    return get_cached_snapshot(
        "ui_weather_page",
        ttl_seconds=120,
        builder=lambda: _build_ui_weather_snapshot(
            canonical_execution=composition.weather_execution,
        ),
    )


def _canonical_weather_ui_unavailable():
    raise RuntimeError("Weather capability is not configured")


def _cached_ui_network_health_snapshot() -> dict[str, object]:
    composition = brain_application_composition(app)
    return get_cached_snapshot(
        "ui_network_health",
        ttl_seconds=30,
        builder=lambda: build_ui_network_health_snapshot(
            canonical_execution=composition.network_execution,
        ),
    )


def _cached_ui_calendar_snapshot(*, limit: int) -> dict[str, object]:
    composition = brain_application_composition(app)
    if composition.calendar_execution is None:
        return {"events": []}

    def build() -> dict[str, object]:
        try:
            return _build_ui_calendar_snapshot(
                limit=limit,
                canonical_execution=composition.calendar_execution,
            )
        except CalendarReadUnavailableError as exc:
            logger.warning(
                "ui_calendar_component_unavailable surface=summary error_code=%s detail=%s",
                exc.error_code,
                exc.detail,
            )
            return build_ui_calendar_unavailable_snapshot()

    return get_cached_snapshot(
        f"ui_calendar_summary:{limit}",
        ttl_seconds=45,
        builder=build,
    )


def _cached_ui_calendar_page_snapshot() -> dict[str, object]:
    composition = brain_application_composition(app)
    if composition.calendar_execution is None:
        raise RuntimeError("Calendar capability is not configured")

    def build() -> dict[str, object]:
        try:
            return _build_ui_calendar_page_snapshot(
                canonical_execution=composition.calendar_execution,
            )
        except CalendarReadUnavailableError as exc:
            logger.warning(
                "ui_calendar_component_unavailable surface=page error_code=%s detail=%s",
                exc.error_code,
                exc.detail,
            )
            return build_ui_calendar_unavailable_page_snapshot(
                timezone_name=composition.calendar_execution.settings.timezone,
            )

    return get_cached_snapshot(
        "ui_calendar_page",
        ttl_seconds=45,
        builder=build,
    )


def _ui_calendar_confirm_impl_cached(payload):
    composition = brain_application_composition(app)
    if composition.calendar_execution is None:
        raise HTTPException(status_code=409, detail="Calendar is disabled in canonical configuration.")
    result = _ui_calendar_confirm_impl(
        payload,
        canonical_execution=composition.calendar_execution,
    )
    if bool(result.get("ok")):
        invalidate_cached_snapshots("ui_calendar_")
    return result


def _build_ui_home_snapshot() -> dict[str, object]:
    composition = brain_application_composition(app)
    home_assistant = build_canonical_ui_home_assistant_snapshot(
        composition.runtime.home_assistant
    )
    return {
        "generated_at": _build_ui_generated_at(),
        "weather": _cached_ui_home_weather_payload(),
        "calendar": _cached_ui_calendar_snapshot(limit=3),
        "network_health": _cached_ui_network_health_snapshot(),
        **home_assistant,
        "escape_hatches": {
            page: [item.model_dump(mode="json") for item in links]
            for page, links in composition.runtime.household.ui.escape_hatches.items()
        },
        "refresh_after_seconds": 60,
    }

def _build_ui_house_snapshot() -> dict[str, object]:
    composition = brain_application_composition(app)
    return build_ui_house_snapshot(
        home_assistant_settings=composition.runtime.home_assistant,
    )


def _ui_house_camera_snapshot_impl(camera_id: str) -> Response:
    composition = brain_application_composition(app)
    return ui_house_camera_snapshot_impl(
        camera_id,
        home_assistant_settings=composition.runtime.home_assistant,
    )


def _build_satellite_ui_config(satellite_id: str | None) -> dict[str, object]:
    composition = brain_application_composition(app)
    return build_satellite_ui_config(
        satellite_id,
        fleet_settings=composition.runtime.satellite_ui,
        household_settings=composition.runtime.household,
    )


def _audio_ui_dependencies():
    composition = brain_application_composition(app)
    return composition


def _resolve_ui_audio_source(source: str | None):
    composition = _audio_ui_dependencies()
    return resolve_ui_audio_source(
        source,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
    )


def _build_ui_audio_snapshot(source: str | None = None, user_id: str | None = None) -> dict[str, object]:
    composition = _audio_ui_dependencies()
    return build_ui_audio_snapshot(
        source,
        user_id,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _build_ui_audio_status_snapshot(source: str | None = None) -> dict[str, object]:
    composition = _audio_ui_dependencies()
    return build_ui_audio_status_snapshot(
        source,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _ui_audio_search_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_search_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _require_stable_alert_ui_request(
    target_source_id: str, request: Request
) -> ResolvedRequestSource:
    resolved = _canonical_http_request_source(target_source_id, request)
    if resolved is None or not resolved.stable:
        raise HTTPException(
            status_code=403,
            detail="Durable alert creation requires an authenticated stable client.",
        )
    target = brain_application_composition(request.app).runtime.satellites.satellite_for_source(
        target_source_id
    )
    if target is None or not target.alert_capable:
        raise HTTPException(
            status_code=400,
            detail="Alert target is not an enabled alert-capable satellite.",
        )
    return resolved


def _ui_audio_play_impl(payload, request: Request | None = None):
    composition = _audio_ui_dependencies()
    if request is not None and payload.sleep_timer_minutes not in (None, 0):
        _require_stable_alert_ui_request(payload.target, request)
    return ui_audio_play_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _ui_audio_control_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_control_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
    )


def _ui_audio_sleep_timer_impl(payload, request: Request | None = None):
    composition = _audio_ui_dependencies()
    if request is not None and payload.operation == "set":
        _require_stable_alert_ui_request(payload.target, request)
    return ui_audio_sleep_timer_impl(
        payload,
        audiobook_execution=composition.audiobook_execution,
    )


def _build_application_satellite_ui_home_snapshot(satellite_id: str | None) -> dict[str, object]:
    composition = brain_application_composition(app)
    payload = build_satellite_ui_home_snapshot(
        satellite_id,
        build_ui_home_snapshot=lambda: {"weather": _cached_ui_home_weather_payload()},
        build_ui_audio_status_snapshot=_build_ui_audio_status_snapshot,
        build_ui_calendar_snapshot=_cached_ui_calendar_snapshot,
        home_assistant_settings=composition.runtime.home_assistant,
        fleet_settings=composition.runtime.satellite_ui,
        household_settings=composition.runtime.household,
        routine_settings=composition.runtime.routines,
    )
    config = build_satellite_ui_config(
        satellite_id,
        fleet_settings=composition.runtime.satellite_ui,
        household_settings=composition.runtime.household,
    )
    payload["timers"] = build_timer_state(
        source_id=str(config.get("source_id") or ""),
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    alarm_state = build_alarm_state(
        source_id=str(config.get("source_id") or ""),
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    payload["alarms"] = alarm_state
    reminder_state = build_reminder_state(
        source_id=str(config.get("source_id") or ""),
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )
    payload["reminders"] = reminder_state
    next_alarm = alarm_state.get("next")
    payload["alarm"] = {
        "active": bool(next_alarm),
        "count": int(alarm_state.get("count") or 0),
        "next": next_alarm,
    }
    return payload


def _ui_timer_action_impl(payload: UiTimerActionRequest, request: Request) -> dict[str, object]:
    _normalize_ui_client_id(payload.client_id)
    resolved = _require_stable_alert_ui_request(payload.source_id, request)
    if resolved.request_source_id != payload.source_id:
        raise HTTPException(status_code=403, detail="Timer action source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    try:
        result = dismiss_timer(
            payload.occurrence_id,
            source_id=payload.source_id,
            household=composition.runtime.household,
            satellites=composition.runtime.satellites,
            idempotency_key=f"ui:{payload.client_id}:{payload.action}:{payload.occurrence_id}",
            db_path=alerts_module.ALERT_DB_PATH,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Active timer occurrence not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        **result,
        "refresh": {"refresh_pages": ["home"], "refresh_after_ms": 0},
    }


def _ui_timer_state_impl(source_id: str, request: Request) -> dict[str, object]:
    resolved = _require_stable_alert_ui_request(source_id, request)
    if resolved.request_source_id != source_id:
        raise HTTPException(status_code=403, detail="Timer state source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    return build_timer_state(
        source_id=source_id,
        household=composition.runtime.household,
        satellites=composition.runtime.satellites,
        db_path=alerts_module.ALERT_DB_PATH,
    )


def _ui_alarm_state_impl(source_id: str, request: Request) -> dict[str, object]:
    resolved = _require_stable_alert_ui_request(source_id, request)
    if resolved.request_source_id != source_id:
        raise HTTPException(status_code=403, detail="Alarm state source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    return build_alarm_state(
        source_id=source_id, household=composition.runtime.household,
        satellites=composition.runtime.satellites, db_path=alerts_module.ALERT_DB_PATH,
    )


def _ui_alarm_action_impl(payload: UiAlarmActionRequest, request: Request) -> dict[str, object]:
    _normalize_ui_client_id(payload.client_id)
    resolved = _require_stable_alert_ui_request(payload.source_id, request)
    if resolved.request_source_id != payload.source_id:
        raise HTTPException(status_code=403, detail="Alarm action source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    try:
        result = manage_alarm(
            source_id=payload.source_id, action=payload.action,
            schedule_id=payload.schedule_id, occurrence_id=payload.occurrence_id,
            snooze_minutes=payload.snooze_minutes,
            household=composition.runtime.household, satellites=composition.runtime.satellites,
            idempotency_key=f"ui:{payload.client_id}:{payload.action}:{payload.occurrence_id or payload.schedule_id}",
            db_path=alerts_module.ALERT_DB_PATH,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alarm not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**result, "refresh": {"refresh_pages": ["home", "alerts"], "refresh_after_ms": 0}}


def _ui_reminder_state_impl(source_id: str, request: Request) -> dict[str, object]:
    resolved = _require_stable_alert_ui_request(source_id, request)
    if resolved.request_source_id != source_id:
        raise HTTPException(status_code=403, detail="Reminder state source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    return build_reminder_state(
        source_id=source_id, household=composition.runtime.household,
        satellites=composition.runtime.satellites, db_path=alerts_module.ALERT_DB_PATH,
    )


def _ui_reminder_action_impl(payload: UiReminderActionRequest, request: Request) -> dict[str, object]:
    _normalize_ui_client_id(payload.client_id)
    resolved = _require_stable_alert_ui_request(payload.source_id, request)
    if resolved.request_source_id != payload.source_id:
        raise HTTPException(status_code=403, detail="Reminder action source does not match the authenticated UI source.")
    composition = brain_application_composition(request.app)
    try:
        result = manage_reminder(
            source_id=payload.source_id, action=payload.action,
            occurrence_id=payload.occurrence_id, snooze_minutes=payload.snooze_minutes,
            household=composition.runtime.household, satellites=composition.runtime.satellites,
            idempotency_key=f"ui:{payload.client_id}:{payload.action}:{payload.occurrence_id}",
            db_path=alerts_module.ALERT_DB_PATH,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Reminder occurrence not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**result, "refresh": {"refresh_pages": ["home", "alerts"], "refresh_after_ms": 0}}


def _build_compact_alert_state(source_id: str, composition) -> dict[str, object]:
    clock = datetime.now(UTC)
    timer_state = build_timer_state(
        source_id=source_id, household=composition.runtime.household,
        satellites=composition.runtime.satellites, now=clock, db_path=alerts_module.ALERT_DB_PATH,
    )
    alarm_state = build_alarm_state(
        source_id=source_id, household=composition.runtime.household,
        satellites=composition.runtime.satellites, now=clock, db_path=alerts_module.ALERT_DB_PATH,
    )
    reminder_state = build_reminder_state(
        source_id=source_id, household=composition.runtime.household,
        satellites=composition.runtime.satellites, now=clock, db_path=alerts_module.ALERT_DB_PATH,
    )
    return {
        "source_id": source_id,
        "generated_at": clock.isoformat(),
        "status": "ready",
        "timer_state": timer_state,
        "alarm_state": alarm_state,
        "reminder_state": reminder_state,
        "timers": list(timer_state.get("timers") or []),
        "alarms": list(alarm_state.get("alarms") or []),
        "reminders": list(reminder_state.get("reminders") or []),
        "ringing": list(alarm_state.get("ringing") or []),
        "outstanding": list(reminder_state.get("outstanding") or []),
        "active_count": (
            int(timer_state.get("count") or 0)
            + int(alarm_state.get("count") or 0)
            + int(reminder_state.get("count") or 0)
        ),
        "refresh_after_seconds": 2
        if timer_state.get("timers") or alarm_state.get("ringing") or reminder_state.get("outstanding")
        else 30,
    }


def _ui_alert_state_impl(source_id: str, request: Request) -> dict[str, object]:
    resolved = _require_stable_alert_ui_request(source_id, request)
    if resolved.request_source_id != source_id:
        raise HTTPException(status_code=403, detail="Alert state source does not match the authenticated UI source.")
    return _build_compact_alert_state(source_id, brain_application_composition(request.app))


def _validate_ui_action_source(source: str | None) -> str:
    if source is None:
        raise HTTPException(status_code=400, detail="A playback-capable source is required for this action")
    selected_source, _configured_sources = _resolve_ui_audio_source(source)
    if selected_source is None:
        raise HTTPException(status_code=400, detail="A playback-capable source is required for this action")
    return selected_source


def _ui_context_start_impl(payload: UiContextStartRequest, request: Request | None = None) -> dict[str, object]:
    target_source_id = str(payload.target_source_id or "").strip()
    if payload.action in {"music_search", "audiobook_search"}:
        target_source_id = _validate_ui_action_source(target_source_id or None)

    request_source_id = ""
    if request is not None:
        resolved_source = _canonical_http_request_source(None, request)
        if resolved_source is not None:
            request_source_id = resolved_source.request_source_id
        if payload.action == "set_alarm":
            if resolved_source is None or not resolved_source.stable:
                raise HTTPException(
                    status_code=403,
                    detail="Durable alert creation requires an authenticated stable client.",
                )
            target_satellite = brain_application_composition(
                request.app
            ).runtime.satellites.satellite_for_source(target_source_id)
            if target_satellite is None or not target_satellite.alert_capable:
                raise HTTPException(
                    status_code=400,
                    detail="target_source_id is not an enabled alert-capable satellite.",
                )

    return ui_context_start_impl(
        payload,
        request_source_id=request_source_id,
        target_source_id=target_source_id,
    )

def _ui_action_impl(payload: UiActionRequest) -> dict[str, object]:
    action_id = str(payload.action_id).strip()
    client_id = _normalize_ui_client_id(payload.client_id)
    composition = brain_application_composition(app)
    home_assistant_settings = composition.runtime.home_assistant
    direct_result = execute_home_assistant_ui_action(
        action_id,
        home_assistant_settings=home_assistant_settings,
    )
    if direct_result is not None:
        return direct_result
    action_spec = resolve_home_assistant_dynamic_ui_action(
        action_id,
        home_assistant_settings=home_assistant_settings,
    )
    if not action_spec:
        raise HTTPException(status_code=404, detail=f"Unknown ui action {action_id}")
    command_text = str(action_spec["command_text"])
    requires_source = bool(action_spec.get("requires_source"))
    action_source = _validate_ui_action_source(payload.source) if requires_source else None
    refresh_pages = list(action_spec.get("refresh_pages") or ["home"])
    response = command_request(
        CommandRequest(
            text=command_text,
            source=action_source or client_id,
            session_id=f"ui-action:{client_id}" if action_source is None else f"ui-action:{client_id}:{action_source}",
            playback_target_source_id=action_source,
        )
    )
    status = str(response.dispatch.status or "")
    if status == "pending_confirmation" and bool(action_spec.get("auto_confirm_pending")):
        response = command_request(
            CommandRequest(
                text="confirm",
                source=action_source or client_id,
                session_id=f"ui-action:{client_id}" if action_source is None else f"ui-action:{client_id}:{action_source}",
                playback_target_source_id=action_source,
            )
        )
        status = str(response.dispatch.status or "")
    ok = status in {"executed", "pending_confirmation", "pending_clarification"}
    result_payload: dict[str, object] = {
        "status": status,
        "message": response.reply_text or ("Action executed." if ok else "Action failed."),
    }
    if response.reply_text:
        result_payload["reply_text"] = response.reply_text

    output: dict[str, object] = {
        "ok": ok,
        "action_id": action_id,
        "result": result_payload,
        "refresh": {"refresh_pages": refresh_pages},
    }
    if not ok:
        dispatch_result = response.dispatch.result or {}
        output["error"] = str(dispatch_result.get("error") or "action_failed")
        output["detail"] = str(dispatch_result.get("detail") or response.reply_text or "Action failed.")
    return output


def _routine_ui_action_adapter(
    *,
    action_id: str,
    client_id: str,
    source_id: str | None = None,
) -> dict[str, object]:
    return _ui_action_impl(
        UiActionRequest(
            action_id=action_id,
            client_id=client_id,
            source=source_id,
        )
    )


def _routine_audiobook_start_adapter(
    *,
    source_id: str,
    user_id: str,
    client_id: str,
    defer_audible_start: bool = False,
    sleep_timer_seconds: int | None = None,
) -> dict[str, object]:
    composition = brain_application_composition(app)
    return start_current_audiobook_for_user(
        client_id=client_id,
        source_id=source_id,
        user_id=user_id,
        defer_audible_start=defer_audible_start,
        sleep_timer_seconds=sleep_timer_seconds,
        audiobook_execution=composition.audiobook_execution,
    )


def _routine_sleep_timer_adapter(
    *,
    source_id: str,
    duration_seconds: int,
    client_id: str,
) -> dict[str, object]:
    composition = brain_application_composition(app)
    return set_audiobook_sleep_timer_seconds(
        client_id=client_id,
        source_id=source_id,
        duration_seconds=duration_seconds,
        audiobook_execution=composition.audiobook_execution,
    )


def _routine_state_check_adapter(
    *,
    check_id: str,
    expected_state: str,
    client_id: str,
) -> dict[str, object]:
    del client_id
    return {
        "ok": False,
        "error": "unknown_state_check",
        "detail": f"Unknown canonical routine state check {check_id}.",
    }


def _routine_playback_check_adapter(
    *,
    source_id: str,
    check_id: str,
    client_id: str,
) -> dict[str, object]:
    del client_id
    if check_id != "routine_audiobook_stopped":
        return {
            "ok": False,
            "error": "unknown_playback_check",
            "detail": f"Unknown curated routine playback check {check_id}.",
        }
    playback_execution = _canonical_playback_execution(source_id)
    if playback_execution is None:
        return {
            "ok": False,
            "error": "audio_not_configured",
            "detail": "Source is not an admitted canonical audio target.",
        }
    authority = playback_execution.fetch_playback_authority(source_id)
    owner = authority.get("output_owner") if isinstance(authority, dict) else None
    owner = owner if isinstance(owner, dict) else {}
    media_kind = str(owner.get("media_kind") or "").strip().lower()
    state_name = str(owner.get("state") or "").strip().lower()
    active = media_kind == "audiobook" and state_name not in {"", "idle", "stopped", "ended", "closed"}
    return {
        "ok": not active,
        "status": "passed" if not active else "failed",
        "media_kind": media_kind or None,
        "playback_state": state_name or None,
        "detail": "Audiobook playback is stopped." if not active else "Audiobook playback is still active.",
    }
