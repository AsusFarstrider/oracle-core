from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response

from .schemas import (
    UiActionRequest,
    UiAlarmCancelRequest,
    UiAlarmActionRequest,
    UiAudioControlRequest,
    UiAudioPlayRequest,
    UiAudioSearchRequest,
    UiAudioSleepTimerRequest,
    UiCalendarDraftCancelRequest,
    UiCalendarDraftConfirmRequest,
    UiCalendarDraftRequest,
    UiContextStartRequest,
    UiTimerActionRequest,
    UiReminderActionRequest,
)


CalendarDraftHandler = Callable[[UiCalendarDraftRequest], dict[str, object]]
CalendarConfirmHandler = Callable[[UiCalendarDraftConfirmRequest], dict[str, object]]
CalendarCancelHandler = Callable[[UiCalendarDraftCancelRequest], dict[str, object]]
AudioSearchHandler = Callable[[UiAudioSearchRequest], dict[str, object]]
AudioPlayHandler = Callable[[UiAudioPlayRequest], dict[str, object]]
AudioControlHandler = Callable[[UiAudioControlRequest], dict[str, object]]
AudioSleepTimerHandler = Callable[[UiAudioSleepTimerRequest], dict[str, object]]
HouseCameraSnapshotHandler = Callable[[str], Response]
UiActionHandler = Callable[[UiActionRequest], dict[str, object]]
UiContextStartHandler = Callable[[UiContextStartRequest, Request | None], dict[str, object]]
UiAlarmCancelHandler = Callable[[UiAlarmCancelRequest], dict[str, object]]
UiTimerActionHandler = Callable[[UiTimerActionRequest, Request], dict[str, object]]
UiTimerStateHandler = Callable[[str, Request], dict[str, object]]
UiAlarmActionHandler = Callable[[UiAlarmActionRequest, Request], dict[str, object]]
UiAlarmStateHandler = Callable[[str, Request], dict[str, object]]
UiReminderActionHandler = Callable[[UiReminderActionRequest, Request], dict[str, object]]
UiReminderStateHandler = Callable[[str, Request], dict[str, object]]
UiAlertStateHandler = Callable[[str, Request], dict[str, object]]

_ui_calendar_draft: CalendarDraftHandler | None = None
_ui_calendar_confirm: CalendarConfirmHandler | None = None
_ui_calendar_cancel: CalendarCancelHandler | None = None
_ui_audio_search: AudioSearchHandler | None = None
_ui_audio_play: AudioPlayHandler | None = None
_ui_audio_control: AudioControlHandler | None = None
_ui_audio_sleep_timer: AudioSleepTimerHandler | None = None
_ui_house_camera_snapshot: HouseCameraSnapshotHandler | None = None
_ui_action: UiActionHandler | None = None
_ui_context_start: UiContextStartHandler | None = None
_ui_alarm_cancel: UiAlarmCancelHandler | None = None
_ui_timer_action: UiTimerActionHandler | None = None
_ui_timer_state: UiTimerStateHandler | None = None
_ui_alarm_action: UiAlarmActionHandler | None = None
_ui_alarm_state: UiAlarmStateHandler | None = None
_ui_reminder_action: UiReminderActionHandler | None = None
_ui_reminder_state: UiReminderStateHandler | None = None
_ui_alert_state: UiAlertStateHandler | None = None


def configure_ui_routes(
    *,
    ui_calendar_draft: CalendarDraftHandler,
    ui_calendar_confirm: CalendarConfirmHandler,
    ui_calendar_cancel: CalendarCancelHandler,
    ui_audio_search: AudioSearchHandler,
    ui_audio_play: AudioPlayHandler,
    ui_audio_control: AudioControlHandler,
    ui_audio_sleep_timer: AudioSleepTimerHandler,
    ui_house_camera_snapshot: HouseCameraSnapshotHandler,
    ui_action: UiActionHandler,
    ui_context_start: UiContextStartHandler,
    ui_alarm_cancel: UiAlarmCancelHandler,
    ui_timer_action: UiTimerActionHandler,
    ui_timer_state: UiTimerStateHandler,
    ui_alarm_action: UiAlarmActionHandler,
    ui_alarm_state: UiAlarmStateHandler,
    ui_reminder_action: UiReminderActionHandler,
    ui_reminder_state: UiReminderStateHandler,
    ui_alert_state: UiAlertStateHandler,
) -> None:
    global _ui_calendar_draft
    global _ui_calendar_confirm
    global _ui_calendar_cancel
    global _ui_audio_search
    global _ui_audio_play
    global _ui_audio_control
    global _ui_audio_sleep_timer
    global _ui_house_camera_snapshot
    global _ui_action
    global _ui_context_start
    global _ui_alarm_cancel
    global _ui_timer_action
    global _ui_timer_state
    global _ui_alarm_action
    global _ui_alarm_state
    global _ui_reminder_action
    global _ui_reminder_state
    global _ui_alert_state

    _ui_calendar_draft = ui_calendar_draft
    _ui_calendar_confirm = ui_calendar_confirm
    _ui_calendar_cancel = ui_calendar_cancel
    _ui_audio_search = ui_audio_search
    _ui_audio_play = ui_audio_play
    _ui_audio_control = ui_audio_control
    _ui_audio_sleep_timer = ui_audio_sleep_timer
    _ui_house_camera_snapshot = ui_house_camera_snapshot
    _ui_action = ui_action
    _ui_context_start = ui_context_start
    _ui_alarm_cancel = ui_alarm_cancel
    _ui_timer_action = ui_timer_action
    _ui_timer_state = ui_timer_state
    _ui_alarm_action = ui_alarm_action
    _ui_alarm_state = ui_alarm_state
    _ui_reminder_action = ui_reminder_action
    _ui_reminder_state = ui_reminder_state
    _ui_alert_state = ui_alert_state


def _require_handler(handler):
    if handler is None:
        raise RuntimeError("UI write/action routes are not configured")
    return handler


def ui_calendar_draft(payload: UiCalendarDraftRequest) -> dict[str, object]:
    return _require_handler(_ui_calendar_draft)(payload)


def ui_calendar_confirm(payload: UiCalendarDraftConfirmRequest) -> dict[str, object]:
    return _require_handler(_ui_calendar_confirm)(payload)


def ui_calendar_cancel(payload: UiCalendarDraftCancelRequest) -> dict[str, object]:
    return _require_handler(_ui_calendar_cancel)(payload)


def ui_audio_search(payload: UiAudioSearchRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_search)(payload)


def ui_audio_play(payload: UiAudioPlayRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_play)(payload)


def ui_audio_play_http(payload: UiAudioPlayRequest, request: Request) -> dict[str, object]:
    return _require_handler(_ui_audio_play)(payload, request)


def ui_audio_control(payload: UiAudioControlRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_control)(payload)


def ui_audio_sleep_timer(payload: UiAudioSleepTimerRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_sleep_timer)(payload)


def ui_audio_sleep_timer_http(
    payload: UiAudioSleepTimerRequest, request: Request
) -> dict[str, object]:
    return _require_handler(_ui_audio_sleep_timer)(payload, request)


def ui_house_camera_snapshot(camera_id: str) -> Response:
    return _require_handler(_ui_house_camera_snapshot)(camera_id)


def ui_action(payload: UiActionRequest) -> dict[str, object]:
    return _require_handler(_ui_action)(payload)


def ui_context_start(payload: UiContextStartRequest) -> dict[str, object]:
    return _require_handler(_ui_context_start)(payload, None)


def ui_context_start_http(payload: UiContextStartRequest, request: Request) -> dict[str, object]:
    return _require_handler(_ui_context_start)(payload, request)


def ui_alarm_cancel(payload: UiAlarmCancelRequest) -> dict[str, object]:
    return _require_handler(_ui_alarm_cancel)(payload)


def ui_timer_action(payload: UiTimerActionRequest, request: Request) -> dict[str, object]:
    return _require_handler(_ui_timer_action)(payload, request)


def ui_timer_state(source_id: str, request: Request) -> dict[str, object]:
    return _require_handler(_ui_timer_state)(source_id, request)


def ui_alarm_action(payload: UiAlarmActionRequest, request: Request) -> dict[str, object]:
    return _require_handler(_ui_alarm_action)(payload, request)


def ui_alarm_state(source_id: str, request: Request) -> dict[str, object]:
    return _require_handler(_ui_alarm_state)(source_id, request)


def ui_reminder_action(payload: UiReminderActionRequest, request: Request) -> dict[str, object]:
    return _require_handler(_ui_reminder_action)(payload, request)


def ui_reminder_state(source_id: str, request: Request) -> dict[str, object]:
    return _require_handler(_ui_reminder_state)(source_id, request)


def ui_alert_state(source_id: str, request: Request) -> dict[str, object]:
    return _require_handler(_ui_alert_state)(source_id, request)


def register_ui_routes(app: FastAPI) -> None:
    app.post("/api/ui/calendar/draft")(ui_calendar_draft)
    app.post("/api/ui/calendar/confirm")(ui_calendar_confirm)
    app.post("/api/ui/calendar/cancel")(ui_calendar_cancel)
    app.post("/api/ui/audio/search")(ui_audio_search)
    app.post("/api/ui/audio/play")(ui_audio_play_http)
    app.post("/api/ui/audio/control")(ui_audio_control)
    app.post("/api/ui/audio/sleep-timer")(ui_audio_sleep_timer_http)
    app.get("/api/ui/house/cameras/{camera_id}/snapshot")(ui_house_camera_snapshot)
    app.post("/api/ui/action")(ui_action)
    app.post("/api/ui/context/start")(ui_context_start_http)
    app.post("/api/ui/alarm/cancel")(ui_alarm_cancel)
    app.post("/api/ui/timer/action")(ui_timer_action)
    app.get("/api/ui/timer/state")(ui_timer_state)
    app.post("/api/ui/alarm/action")(ui_alarm_action)
    app.get("/api/ui/alarm/state")(ui_alarm_state)
    app.post("/api/ui/reminder/action")(ui_reminder_action)
    app.get("/api/ui/reminder/state")(ui_reminder_state)
    app.get("/api/ui/alert/state")(ui_alert_state)
