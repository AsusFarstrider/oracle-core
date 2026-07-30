from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response

from .schemas import (
    UiActionRequest,
    UiAlarmCancelRequest,
    UiAudioControlRequest,
    UiAudioPlayRequest,
    UiAudioSearchRequest,
    UiAudioSleepTimerRequest,
    UiCalendarDraftCancelRequest,
    UiCalendarDraftConfirmRequest,
    UiCalendarDraftRequest,
    UiContextStartRequest,
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


def ui_audio_control(payload: UiAudioControlRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_control)(payload)


def ui_audio_sleep_timer(payload: UiAudioSleepTimerRequest) -> dict[str, object]:
    return _require_handler(_ui_audio_sleep_timer)(payload)


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


def register_ui_routes(app: FastAPI) -> None:
    app.post("/api/ui/calendar/draft")(ui_calendar_draft)
    app.post("/api/ui/calendar/confirm")(ui_calendar_confirm)
    app.post("/api/ui/calendar/cancel")(ui_calendar_cancel)
    app.post("/api/ui/audio/search")(ui_audio_search)
    app.post("/api/ui/audio/play")(ui_audio_play)
    app.post("/api/ui/audio/control")(ui_audio_control)
    app.post("/api/ui/audio/sleep-timer")(ui_audio_sleep_timer)
    app.get("/api/ui/house/cameras/{camera_id}/snapshot")(ui_house_camera_snapshot)
    app.post("/api/ui/action")(ui_action)
    app.post("/api/ui/context/start")(ui_context_start_http)
    app.post("/api/ui/alarm/cancel")(ui_alarm_cancel)
