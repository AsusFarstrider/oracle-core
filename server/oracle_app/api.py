from __future__ import annotations

from fastapi import Request

from .admin_diagnostics_routes import register_admin_diagnostics_routes
from .admin_facts_routes import register_admin_facts_routes
from .admin_home_automation_routes import register_admin_home_automation_routes
from .admin_network_routes import register_admin_network_routes
from .admin_notifications_routes import register_admin_notifications_routes
from .admin_orchestration_routes import register_admin_orchestration_routes
from .admin_suggestions_routes import register_admin_suggestions_routes
from .application_command import (
    command_events,
    command_http_request,
    route_http_request,
    session_lookup,
)
from .application_playback import deferred_resume
from .application_runtime import admin_cache_diagnostics, app
from .application_speech import synthesize_speech, transcribe_audio
from .application_ui import (
    _build_application_satellite_ui_home_snapshot,
    _build_satellite_ui_config,
    _build_ui_audio_snapshot,
    _build_ui_audio_status_snapshot,
    _build_ui_home_snapshot,
    _build_ui_house_snapshot,
    _cached_ui_calendar_page_snapshot,
    _cached_ui_weather_snapshot,
    _routine_audiobook_start_adapter,
    _routine_playback_check_adapter,
    _routine_sleep_timer_adapter,
    _routine_state_check_adapter,
    _routine_ui_action_adapter,
    _ui_action_impl,
    _ui_audio_control_impl,
    _ui_audio_play_impl,
    _ui_audio_search_impl,
    _ui_audio_sleep_timer_impl,
    _ui_calendar_confirm_impl_cached,
    _ui_context_start_impl,
    _ui_house_camera_snapshot_impl,
    _ui_timer_action_impl,
    _ui_timer_state_impl,
    _ui_alarm_action_impl,
    _ui_alarm_state_impl,
    _ui_reminder_action_impl,
    _ui_reminder_state_impl,
    _ui_alert_state_impl,
)
from .browser_routes import register_browser_routes
from .conversation_routes import register_conversation_routes
from .home_automation_routes import register_home_automation_routes
from .media_routes import register_media_routes
from .memory.correlation import correlation_context
from .orchestration_recovery import register_orchestration_recovery_routes
from .orchestration_routine_routes import register_orchestration_routine_routes
from .orchestration_routines import configure_routine_adapters
from .satellite_activity_routes import register_satellite_activity_routes
from .satellite_alert_routes import register_satellite_alert_routes
from .satellite_playback_routes import register_satellite_playback_routes
from .satellite_projection_routes import register_satellite_projection_routes
from .speech_routes import register_speech_routes
from .ui_calendar import (
    ui_calendar_cancel_impl as _ui_calendar_cancel_impl,
    ui_calendar_draft_impl as _ui_calendar_draft_impl,
)
from .ui_context import ui_alarm_cancel_impl as _ui_alarm_cancel_impl
from .ui_routes import configure_ui_routes, register_ui_routes
from .ui_snapshot_routes import configure_ui_snapshot_routes, register_ui_snapshot_routes
from .wake_arbitration_routes import register_wake_arbitration_routes
from .wake_capture_upload_routes import register_wake_capture_upload_routes
from .health_routes import register_health_routes


@app.middleware("http")
async def attach_correlation_id(request: Request, call_next):
    inbound = request.headers.get("X-Oracle-Correlation-Id")
    with correlation_context(inbound) as correlation_id:
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Oracle-Correlation-Id"] = correlation_id
        return response


# Visible application assembly: route families remain grouped by their public
# semantic boundary while execution lives in the named application owners.
register_browser_routes(app)

register_health_routes(app)
app.get("/api/admin/caches")(admin_cache_diagnostics)
register_admin_diagnostics_routes(app)
register_admin_facts_routes(app)
register_admin_home_automation_routes(app)
register_admin_notifications_routes(app)
register_admin_network_routes(app)
register_admin_orchestration_routes(app)
register_admin_suggestions_routes(app)

register_satellite_activity_routes(app)
register_satellite_alert_routes(app)
register_satellite_projection_routes(app)
register_wake_capture_upload_routes(app)
register_wake_arbitration_routes(app)
register_home_automation_routes(app)
register_media_routes(app)

configure_ui_snapshot_routes(
    build_ui_home_snapshot=_build_ui_home_snapshot,
    build_satellite_ui_config=_build_satellite_ui_config,
    build_satellite_ui_home_snapshot=_build_application_satellite_ui_home_snapshot,
    build_ui_weather_snapshot=_cached_ui_weather_snapshot,
    build_ui_calendar_page_snapshot=_cached_ui_calendar_page_snapshot,
    build_ui_audio_snapshot=_build_ui_audio_snapshot,
    build_ui_audio_status_snapshot=_build_ui_audio_status_snapshot,
    build_ui_house_snapshot=_build_ui_house_snapshot,
)
register_ui_snapshot_routes(app)
configure_ui_routes(
    ui_calendar_draft=_ui_calendar_draft_impl,
    ui_calendar_confirm=_ui_calendar_confirm_impl_cached,
    ui_calendar_cancel=_ui_calendar_cancel_impl,
    ui_audio_search=_ui_audio_search_impl,
    ui_audio_play=_ui_audio_play_impl,
    ui_audio_control=_ui_audio_control_impl,
    ui_audio_sleep_timer=_ui_audio_sleep_timer_impl,
    ui_house_camera_snapshot=_ui_house_camera_snapshot_impl,
    ui_action=_ui_action_impl,
    ui_context_start=_ui_context_start_impl,
    ui_alarm_cancel=_ui_alarm_cancel_impl,
    ui_timer_action=_ui_timer_action_impl,
    ui_timer_state=_ui_timer_state_impl,
    ui_alarm_action=_ui_alarm_action_impl,
    ui_alarm_state=_ui_alarm_state_impl,
    ui_reminder_action=_ui_reminder_action_impl,
    ui_reminder_state=_ui_reminder_state_impl,
    ui_alert_state=_ui_alert_state_impl,
)
register_ui_routes(app)
register_orchestration_recovery_routes(app)
register_orchestration_routine_routes(app)
configure_routine_adapters(
    ui_action=_routine_ui_action_adapter,
    audiobook_start=_routine_audiobook_start_adapter,
    sleep_timer=_routine_sleep_timer_adapter,
    state_check=_routine_state_check_adapter,
    playback_check=_routine_playback_check_adapter,
)

register_conversation_routes(
    app,
    route_request=route_http_request,
    command_request=command_http_request,
    session_lookup=session_lookup,
    command_events=command_events,
)
register_speech_routes(
    app,
    synthesize_speech=synthesize_speech,
    transcribe_audio=transcribe_audio,
)
register_satellite_playback_routes(app, deferred_resume=deferred_resume)
