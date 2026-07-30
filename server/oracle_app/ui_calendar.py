from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import state
from .calendar_runtime import CanonicalCalendarExecution
from .calendar_write import build_confirmation_prompt
from .schemas import (
    UiCalendarDraftCancelRequest,
    UiCalendarDraftConfirmRequest,
    UiCalendarDraftRequest,
)


def _normalize_ui_client_id(client_id: str) -> str:
    normalized = str(client_id or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="client_id cannot be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    if normalized.lower() != normalized or any(char not in allowed for char in normalized):
        raise HTTPException(
            status_code=400,
            detail="client_id must be lowercase, hyphen-separated, and contain only letters, numbers, and hyphens",
        )
    return normalized


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _serialize_ui_calendar_event(event) -> dict[str, object]:
    return {
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": bool(getattr(event, "all_day", False)),
    }


def _normalize_ui_calendar_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("date is required")
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError("date must be in YYYY-MM-DD format") from exc


def _normalize_ui_calendar_time(value: str, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.strptime(raw, "%H:%M")
        return parsed.strftime("%H:%M")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in HH:MM format") from exc


def _add_minutes_to_ui_calendar_time(start_time: str, duration_minutes: int) -> str:
    start = datetime.strptime(start_time, "%H:%M")
    end = start + timedelta(minutes=duration_minutes)
    return end.strftime("%H:%M")


def _validate_ui_calendar_draft_input(payload: UiCalendarDraftRequest) -> tuple[dict[str, object] | None, dict[str, str]]:
    errors: dict[str, str] = {}
    title = str(payload.title or "").strip()
    if not title:
        errors["title"] = "Title is required."

    try:
        normalized_date = _normalize_ui_calendar_date(payload.date)
    except ValueError as exc:
        normalized_date = ""
        errors["date"] = str(exc)

    all_day = bool(payload.all_day)
    start_time: str | None = None
    end_time: str | None = None
    duration_minutes: int | None = None

    if all_day:
        if str(payload.start_time or "").strip():
            errors["start_time"] = "All-day events cannot include a start time."
        if str(payload.end_time or "").strip():
            errors["end_time"] = "All-day events cannot include an end time."
        if payload.duration_minutes is not None:
            errors["duration_minutes"] = "All-day events cannot include a duration."
    else:
        try:
            start_time = _normalize_ui_calendar_time(payload.start_time or "", field_name="start_time")
        except ValueError as exc:
            errors["start_time"] = str(exc)

        raw_end_time = str(payload.end_time or "").strip()
        has_end_time = bool(raw_end_time)
        has_duration = payload.duration_minutes is not None
        if not has_end_time and not has_duration:
            errors["end_time"] = "Timed events require either an end time or a duration."
        if has_end_time and has_duration:
            errors["duration_minutes"] = "Choose either an end time or a duration, not both."
        if has_end_time:
            try:
                end_time = _normalize_ui_calendar_time(raw_end_time, field_name="end_time")
            except ValueError as exc:
                errors["end_time"] = str(exc)
        if has_duration:
            try:
                duration_minutes = int(payload.duration_minutes) if payload.duration_minutes is not None else None
            except (TypeError, ValueError):
                duration_minutes = None
                errors["duration_minutes"] = "Duration must be a whole number of minutes."
            else:
                if duration_minutes is None or duration_minutes <= 0:
                    errors["duration_minutes"] = "Duration must be greater than zero."
                elif duration_minutes > (24 * 60):
                    errors["duration_minutes"] = "Duration must be 24 hours or less."

        if start_time and duration_minutes is not None and end_time is None:
            end_time = _add_minutes_to_ui_calendar_time(start_time, duration_minutes)

    if errors:
        return None, errors

    event_draft: dict[str, object] = {
        "title": title,
        "date": normalized_date,
        "all_day": all_day,
    }
    if not all_day:
        event_draft["start_time"] = start_time
        event_draft["end_time"] = end_time
        event_draft["duration_minutes"] = duration_minutes
    return event_draft, {}


def _build_ui_calendar_confirmation_payload(draft_id: str, event_draft: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "stage": "confirmation",
        "draft_id": draft_id,
        "draft": {
            "title": str(event_draft.get("title") or ""),
            "date": str(event_draft.get("date") or ""),
            "all_day": bool(event_draft.get("all_day")),
            "start_time": event_draft.get("start_time"),
            "end_time": event_draft.get("end_time"),
            "duration_minutes": event_draft.get("duration_minutes"),
        },
        "confirmation": {
            "message": build_confirmation_prompt(event_draft),
        },
        "refresh": {"refresh_pages": []},
    }


def build_ui_calendar_snapshot(
    *,
    limit: int,
    canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    timezone_name = canonical_execution.settings.timezone
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    loaded = canonical_execution.load_events(scope="personal").value
    return serialize_ui_calendar_snapshot(loaded=loaded, now=now, limit=limit)


def serialize_ui_calendar_snapshot(*, loaded, now: datetime, limit: int) -> dict[str, object]:
    upcoming = [event for event in loaded if event.end > now]
    upcoming.sort(key=lambda item: item.start)
    events = [_serialize_ui_calendar_event(event) for event in upcoming[:limit]]
    return {"events": events}


def build_ui_calendar_page_snapshot(
    *,
    canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    timezone_name = canonical_execution.settings.timezone
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    loaded = canonical_execution.load_events(scope="personal").value
    return serialize_ui_calendar_page_snapshot(
        loaded=loaded,
        now=now,
        timezone_name=timezone_name,
        write_enabled=canonical_execution.settings.write.enabled,
    )


def serialize_ui_calendar_page_snapshot(
    *,
    loaded,
    now: datetime,
    timezone_name: str,
    write_enabled: bool,
) -> dict[str, object]:
    timezone = ZoneInfo(timezone_name)
    upcoming = [event for event in loaded if event.end > now]
    upcoming.sort(key=lambda item: item.start)
    today = now.date()
    today_events = [
        event
        for event in upcoming
        if event.start.astimezone(timezone).date() <= today <= event.end.astimezone(timezone).date()
    ]
    return {
        "generated_at": _build_ui_generated_at(),
        "timezone": timezone_name,
        "today": {
            "date": today.isoformat(),
            "events": [_serialize_ui_calendar_event(event) for event in today_events[:10]],
        },
        "upcoming": {
            "events": [_serialize_ui_calendar_event(event) for event in upcoming[:20]],
        },
        "create_event": {
            "available": write_enabled,
            "status": (
                "available"
                if write_enabled
                else "unavailable"
            ),
            "detail": (
                "Create an event inline, review the normalized draft, and confirm before Oracle commits it."
                if write_enabled
                else "Calendar write is not configured for House Mode right now."
            ),
        },
        "refresh_after_seconds": 120,
    }


def ui_calendar_draft_impl(payload: UiCalendarDraftRequest) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    event_draft, field_errors = _validate_ui_calendar_draft_input(payload)
    if event_draft is None:
        return {
            "ok": False,
            "stage": "validation",
            "error": "validation_failed",
            "detail": "Please fix the highlighted fields.",
            "validation": {"field_errors": field_errors},
            "refresh": {"refresh_pages": []},
        }

    draft_id = f"ui-cal-{uuid.uuid4().hex}"
    state.clear_ui_calendar_drafts_for_client(client_id)
    state.store_ui_calendar_draft(client_id, draft_id, event_draft)
    return _build_ui_calendar_confirmation_payload(draft_id, event_draft)


def ui_calendar_confirm_impl(
    payload: UiCalendarDraftConfirmRequest,
    *,
    canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    return _confirm_calendar_draft(payload, canonical_execution.commit_event)


def _confirm_calendar_draft(payload: UiCalendarDraftConfirmRequest, commit_event) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    draft_id = str(payload.draft_id).strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id cannot be empty")
    event_draft = state.load_ui_calendar_draft(client_id, draft_id)
    if event_draft is None:
        return {
            "ok": False,
            "draft_id": draft_id,
            "error": "draft_not_found",
            "detail": "That calendar draft is no longer available.",
            "refresh": {"refresh_pages": []},
        }

    try:
        committed = commit_event(event_draft)
    except RuntimeError as exc:
        return {
            "ok": False,
            "draft_id": draft_id,
            "error": "calendar_write_failed",
            "detail": str(exc),
            "refresh": {"refresh_pages": []},
        }

    state.clear_ui_calendar_draft(client_id, draft_id)
    committed_draft = committed.get("event_draft") or event_draft
    return {
        "ok": True,
        "draft_id": draft_id,
        "result": {
            "status": "executed",
            "message": f"Added {str(committed_draft.get('title') or 'the event')}.",
        },
        "committed_event": {
            "title": str(committed_draft.get("title") or ""),
            "date": str(committed_draft.get("date") or ""),
            "all_day": bool(committed_draft.get("all_day")),
            "start_time": committed_draft.get("start_time"),
            "end_time": committed_draft.get("end_time"),
        },
        "refresh": {"refresh_pages": ["calendar", "home"]},
    }


def ui_calendar_cancel_impl(payload: UiCalendarDraftCancelRequest) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    draft_id = str(payload.draft_id).strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id cannot be empty")
    cleared = state.clear_ui_calendar_draft(client_id, draft_id)
    return {
        "ok": True,
        "draft_id": draft_id,
        "result": {
            "status": "canceled",
            "message": "Calendar draft cleared." if cleared else "Calendar draft was already cleared.",
        },
        "refresh": {"refresh_pages": []},
    }
