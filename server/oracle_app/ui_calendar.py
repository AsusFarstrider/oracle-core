from __future__ import annotations

import uuid
import hashlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import ui_calendar_drafts
from .calendar_runtime import CanonicalCalendarExecution
from .calendar_write import build_confirmation_prompt
from .temporal import resolve_local_wall_time
from .schemas import (
    UiCalendarDraftCancelRequest,
    UiCalendarDraftConfirmRequest,
    UiCalendarDraftRequest,
    UiCalendarMutationDraftRequest,
    UiCalendarMutationConfirmRequest,
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
    source_id = str(getattr(event, "source_id", ""))
    return {
        "summary": event.summary,
        "event_ref": _calendar_event_ref(
            source_id=source_id,
            uid=event.uid,
            recurrence_id=getattr(event, "recurrence_id", None),
        ),
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": bool(getattr(event, "all_day", False)),
        "location": getattr(event, "location", ""),
        "source_id": source_id,
        "source_label": getattr(event, "source_label", ""),
        "recurring": bool(getattr(event, "recurring", False)),
    }


def _calendar_event_ref(*, source_id: str, uid: str, recurrence_id: str | None) -> str:
    material = "\x1f".join((str(source_id), str(uid), str(recurrence_id or "")))
    return f"calendar-event-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


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
    snapshot = canonical_execution.load_calendar(scope="personal")
    return serialize_ui_calendar_snapshot(
        loaded=snapshot.events, now=now, limit=limit,
        source_availability=[item.__dict__ for item in snapshot.sources],
        freshness=snapshot.freshness,
        provider_age_seconds=snapshot.age_seconds,
        complete=snapshot.complete,
    )


def build_ui_calendar_unavailable_snapshot() -> dict[str, object]:
    return {
        "events": [],
        "status": "unavailable",
        "detail": "Calendar is temporarily unavailable.",
    }


def serialize_ui_calendar_snapshot(
    *, loaded, now: datetime, limit: int,
    source_availability: list[dict[str, object]] | None = None,
    freshness: str = "fresh", provider_age_seconds: float = 0.0,
    complete: bool = True,
) -> dict[str, object]:
    upcoming = [event for event in loaded if event.end > now]
    upcoming.sort(key=lambda item: item.start)
    events = [_serialize_ui_calendar_event(event) for event in upcoming[:limit]]
    return {
        "events": events,
        "freshness": freshness,
        "provider_age_seconds": round(provider_age_seconds, 3),
        "complete": complete,
        "source_availability": source_availability or [],
    }


def build_ui_calendar_page_snapshot(
    *,
    canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    timezone_name = canonical_execution.settings.timezone
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    snapshot = canonical_execution.load_calendar(scope="personal")
    return serialize_ui_calendar_page_snapshot(
        loaded=snapshot.events,
        now=now,
        timezone_name=timezone_name,
        write_enabled=canonical_execution.settings.write.enabled,
        source_availability=[item.__dict__ for item in snapshot.sources],
        freshness=snapshot.freshness,
        provider_age_seconds=snapshot.age_seconds,
        complete=snapshot.complete,
    )


def build_ui_calendar_unavailable_page_snapshot(
    *,
    timezone_name: str,
) -> dict[str, object]:
    timezone = ZoneInfo(timezone_name)
    today = datetime.now(timezone).date()
    return {
        "generated_at": _build_ui_generated_at(),
        "timezone": timezone_name,
        "status": "unavailable",
        "freshness": "unavailable",
        "provider_age_seconds": None,
        "complete": False,
        "source_availability": [],
        "detail": "Calendar is temporarily unavailable.",
        "today": {"date": today.isoformat(), "events": []},
        "upcoming": {"events": []},
        "create_event": {
            "available": False,
            "status": "unavailable",
            "detail": "Calendar is temporarily unavailable.",
        },
        "refresh_after_seconds": 30,
    }


def serialize_ui_calendar_page_snapshot(
    *,
    loaded,
    now: datetime,
    timezone_name: str,
    write_enabled: bool,
    source_availability: list[dict[str, object]] | None = None,
    freshness: str = "fresh",
    provider_age_seconds: float = 0.0,
    complete: bool = True,
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
        "freshness": freshness,
        "provider_age_seconds": round(provider_age_seconds, 3),
        "source_availability": source_availability or [],
        "complete": complete,
        "status": "available" if complete else "partial",
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
    ui_calendar_drafts.clear_ui_calendar_drafts_for_client(client_id)
    ui_calendar_drafts.store_ui_calendar_draft(client_id, draft_id, event_draft)
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
    event_draft = ui_calendar_drafts.load_ui_calendar_draft(client_id, draft_id)
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

    ui_calendar_drafts.clear_ui_calendar_draft(client_id, draft_id)
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


def ui_calendar_mutation_draft_impl(
    payload: UiCalendarMutationDraftRequest,
    *, canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    if payload.calendar_id != canonical_execution.settings.write.feed_id:
        raise HTTPException(status_code=409, detail="Selected Calendar source is not writable.")
    snapshot = canonical_execution.load_calendar(
        scope="personal", calendar_id=payload.calendar_id, require_config=True,
        force_refresh=True, allow_stale=False,
    )
    matches = [
        event for event in snapshot.events
        if _calendar_event_ref(
            source_id=str(event.source_id), uid=event.uid,
            recurrence_id=event.recurrence_id,
        ) == payload.event_ref
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="Calendar event selection is missing or ambiguous.")
    event = matches[0]
    if event.recurring and payload.recurrence_scope is None:
        return {"ok": False, "stage": "clarification", "error": "recurrence_scope_required", "detail": "Choose this occurrence or the whole series.", "options": ["occurrence", "series"]}
    scope = payload.recurrence_scope or "series"
    changes: dict[str, object] = {}
    if payload.operation == "edit":
        if payload.title is not None:
            title = payload.title.strip()
            if not title:
                raise HTTPException(status_code=422, detail="Title cannot be empty.")
            changes["title"] = title
        temporal_change = any(value is not None for value in (payload.date, payload.all_day, payload.start_time, payload.end_time, payload.duration_minutes))
        if temporal_change:
            zone = ZoneInfo(canonical_execution.settings.timezone)
            local_start, local_end = event.start.astimezone(zone), event.end.astimezone(zone)
            event_date = local_start.date() if payload.date is None else date.fromisoformat(_normalize_ui_calendar_date(payload.date))
            all_day = event.all_day if payload.all_day is None else payload.all_day
            if all_day:
                start = resolve_local_wall_time(event_date, datetime.min.time(), canonical_execution.settings.timezone)
                end = start + timedelta(days=1)
            else:
                start_text = payload.start_time or local_start.strftime("%H:%M")
                start_value = datetime.strptime(_normalize_ui_calendar_time(start_text, field_name="start_time"), "%H:%M").time()
                start = resolve_local_wall_time(event_date, start_value, canonical_execution.settings.timezone)
                if payload.end_time is not None and payload.duration_minutes is not None:
                    raise HTTPException(status_code=422, detail="Choose an end time or duration, not both.")
                if payload.end_time is not None:
                    end_value = datetime.strptime(_normalize_ui_calendar_time(payload.end_time, field_name="end_time"), "%H:%M").time()
                    end = resolve_local_wall_time(event_date, end_value, canonical_execution.settings.timezone)
                    if end <= start:
                        end += timedelta(days=1)
                elif payload.duration_minutes is not None:
                    if not 1 <= payload.duration_minutes <= 1440:
                        raise HTTPException(status_code=422, detail="Duration must be 1 to 1440 minutes.")
                    end = start + timedelta(minutes=payload.duration_minutes)
                else:
                    end = start + (local_end - local_start)
            changes.update(start=start, end=end, all_day=all_day)
        if not changes:
            raise HTTPException(status_code=422, detail="No supported Calendar field change was supplied.")
    mutation = {
        "operation": payload.operation, "uid": event.uid, "source_id": event.source_id,
        "calendar_id": payload.calendar_id,
        "request_source_id": payload.source_id,
        "ui_session_id": payload.ui_session_id,
        "person_id": canonical_execution.settings.effective_person_id("", source_id=payload.source_id),
        "recurrence_scope": scope,
        "recurrence_id": event.recurrence_id or (event.start.isoformat() if event.recurring and scope == "occurrence" else None),
        "changes": changes, "summary": event.summary, "original_start": event.start.isoformat(),
    }
    draft_id = f"ui-cal-mutation-{uuid.uuid4().hex}"
    ui_calendar_drafts.clear_ui_calendar_drafts_for_client(client_id)
    ui_calendar_drafts.store_ui_calendar_draft(client_id, draft_id, {"mutation": mutation})
    return {
        "ok": True, "stage": "confirmation", "draft_id": draft_id,
        "draft": {
            "operation": payload.operation, "source_id": event.source_id,
            "recurrence_scope": scope, "summary": event.summary,
            "changes": changes,
        },
        "confirmation": {"message": f"Confirm {payload.operation} for {event.summary}?"},
        "refresh": {"refresh_pages": []},
    }


def ui_calendar_mutation_confirm_impl(
    payload: UiCalendarMutationConfirmRequest,
    *, canonical_execution: CanonicalCalendarExecution,
) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    draft = ui_calendar_drafts.load_ui_calendar_draft(client_id, payload.draft_id)
    mutation = None if draft is None else draft.get("mutation")
    if not isinstance(mutation, dict):
        return {"ok": False, "error": "draft_not_found", "draft_id": payload.draft_id, "refresh": {"refresh_pages": []}}
    if (
        mutation.get("request_source_id") != payload.source_id
        or mutation.get("ui_session_id") != payload.ui_session_id
    ):
        raise HTTPException(status_code=403, detail="Calendar mutation draft belongs to another source or UI session.")
    result = canonical_execution.mutate_event(mutation)
    ui_calendar_drafts.clear_ui_calendar_draft(client_id, payload.draft_id)
    return {
        "ok": True, "draft_id": payload.draft_id,
        "result": {
            "status": "executed", "operation": mutation.get("operation"),
            "deleted": bool(result.get("deleted")),
        },
        "refresh": {"refresh_pages": ["calendar", "home"]},
    }


def ui_calendar_cancel_impl(payload: UiCalendarDraftCancelRequest) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    draft_id = str(payload.draft_id).strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id cannot be empty")
    cleared = ui_calendar_drafts.clear_ui_calendar_draft(client_id, draft_id)
    return {
        "ok": True,
        "draft_id": draft_id,
        "result": {
            "status": "canceled",
            "message": "Calendar draft cleared." if cleared else "Calendar draft was already cleared.",
        },
        "refresh": {"refresh_pages": []},
    }
