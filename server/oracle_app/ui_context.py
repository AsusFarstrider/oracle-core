from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException

from . import state
from .alerts import build_alert_response, cancel_alerts, format_duration, parse_duration
from .schemas import CommandResponse, DispatchPlan, RouteResponse, UiAlarmCancelRequest, UiAudioSearchRequest, UiContextStartRequest


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def ui_context_start_impl(
    payload: UiContextStartRequest,
    *,
    request_source_id: str | None = None,
    target_source_id: str | None = None,
) -> dict[str, object]:
    source = str(request_source_id or payload.source or "").strip()
    session_id = str(payload.ui_session_id or payload.session_id or "").strip()
    target = str(target_source_id or payload.target_source_id or payload.source or "").strip()
    client_id = str(payload.client_id or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="request source is required")
    if not session_id:
        raise HTTPException(status_code=400, detail="ui_session_id is required")
    if not target:
        raise HTTPException(status_code=400, detail="target_source_id is required")
    prompts = {
        "set_alarm": "What time would you like to set an alarm for?",
        "music_search": "What music would you like?",
        "audiobook_search": "What audiobook would you like?",
    }
    if payload.action not in prompts:
        raise HTTPException(status_code=400, detail=f"Unsupported UI context action {payload.action}")

    prompt = prompts[payload.action]
    stored = state.store_pending_ui_context(
        source,
        session_id,
        {
            "action": payload.action,
            "client_id": client_id,
            "target_source_id": target,
            "prompt": prompt,
            "created_at": _build_ui_generated_at(),
        },
    )
    if not stored:
        raise HTTPException(status_code=400, detail="Unable to start UI context without a source and session id")
    return {
        "ok": True,
        "action": payload.action,
        "source_id": source,
        "ui_session_id": session_id,
        "target_source_id": target,
        "source": source,
        "session_id": session_id,
        "prompt": prompt,
        "refresh": {
            "refresh_pages": ["home"],
            "refresh_after_ms": 0,
        },
    }


def ui_alarm_cancel_impl(payload: UiAlarmCancelRequest) -> dict[str, object]:
    source = str(payload.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    canceled = cancel_alerts(source, "alarm", all_matches=False)
    return {
        "ok": True,
        "source": source,
        "canceled": canceled,
        "message": "Alarm cleared." if canceled else "No alarm set.",
        "refresh": {
            "refresh_pages": ["home"],
            "refresh_after_ms": 0,
        },
    }


def handle_pending_ui_context(
    payload_text: str,
    source: str | None,
    session_id: str | None,
    *,
    audio_search: Callable[[UiAudioSearchRequest], dict[str, object]],
    routine_start: Callable[..., dict[str, object]] | None = None,
) -> CommandResponse | None:
    pending = state.load_pending_ui_context(source, session_id)
    if pending is None:
        return None

    action = str(pending.get("action") or "").strip()
    target_source_id = str(pending.get("target_source_id") or source or "").strip()
    normalized = " ".join(str(payload_text or "").strip().split())
    route = RouteResponse(
        target="system",
        confidence=1.0,
        reason="Matched pending UI context",
        normalized_text=normalized.lower(),
    )

    if normalized.lower() in {"cancel", "never mind", "nevermind", "stop"}:
        state.clear_pending_ui_context(source, session_id)
        dispatch = DispatchPlan(
            target="system",
            hook="ui_context.handle_pending",
            payload={"action": action, "source": source, "session_id": session_id},
            status="executed",
            result={"action": "cancel_pending_ui_context", "ui_context_action": action},
        )
        return CommandResponse(
            route=route,
            dispatch=dispatch,
            reply_text="Okay, I canceled that.",
            session_id=session_id,
            effective_session_id=session_id,
        )

    if action in {"music_search", "audiobook_search"}:
        kind = "music" if action == "music_search" else "audiobook"
        try:
            search_payload = audio_search(
                UiAudioSearchRequest(
                    client_id=str(pending.get("client_id") or f"satellite-ui-{source or ''}"),
                    source=target_source_id,
                    kind=kind,
                    query=normalized,
                )
            )
        except Exception as exc:
            dispatch = DispatchPlan(
                target="system",
                hook="ui_context.handle_pending",
                payload={"action": action, "source": source, "session_id": session_id, "target_source_id": target_source_id, "text": normalized},
                status="failed",
                result={"action": action, "error": "search_failed", "detail": str(exc)},
            )
            return CommandResponse(
                route=route,
                dispatch=dispatch,
                reply_text="I could not search that right now.",
                session_id=session_id,
                effective_session_id=session_id,
            )
        state.clear_pending_ui_context(source, session_id)
        count = int(search_payload.get("result_count") or 0)
        reply = f"I found {count} {'result' if count == 1 else 'results'}."
        dispatch = DispatchPlan(
            target="system",
            hook="ui_context.handle_pending",
            payload={"action": action, "source": source, "session_id": session_id, "target_source_id": target_source_id, "text": normalized},
            status="executed",
            result={
                "action": action,
                "ui_context_action": action,
                "search": search_payload,
                "speech": reply,
            },
        )
        return CommandResponse(
            route=route,
            dispatch=dispatch,
            reply_text=reply,
            session_id=session_id,
            effective_session_id=session_id,
        )

    if action == "routine_input":
        spec = pending.get("input_spec") if isinstance(pending.get("input_spec"), dict) else {}
        no_timer = normalized.casefold() == "no timer"
        value = spec.get("no_timer_value") if no_timer else parse_duration(normalized)
        minimum = int(spec.get("minimum") or 0)
        maximum = int(spec.get("maximum") or 0)
        if value is None or not minimum <= int(value) <= maximum:
            dispatch = DispatchPlan(
                target="system",
                hook="ui_context.handle_pending",
                payload={"action": action, "source": source, "session_id": session_id},
                status="pending_clarification",
                result={"action": action, "error": "routine_duration_required"},
            )
            return CommandResponse(
                route=route,
                dispatch=dispatch,
                reply_text=f"Please say a duration up to {maximum // 60} minutes, or say no timer.",
                session_id=session_id,
                effective_session_id=session_id,
            )
        if routine_start is None:
            raise RuntimeError("Routine input continuation is not configured.")
        try:
            run = routine_start(
                routine_id=str(pending.get("routine_id") or ""),
                client_id=str(pending.get("client_id") or "ui-routine"),
                inputs={str(pending.get("input_id") or ""): int(value)},
            )
        except Exception as exc:
            dispatch = DispatchPlan(
                target="system",
                hook="ui_context.handle_pending",
                payload={"action": action, "source": source, "session_id": session_id},
                status="failed",
                result={"action": action, "error": "routine_start_failed", "detail": str(exc)},
            )
            return CommandResponse(
                route=route,
                dispatch=dispatch,
                reply_text="I could not start the bedtime routine.",
                session_id=session_id,
                effective_session_id=session_id,
            )
        state.clear_pending_ui_context(source, session_id)
        status = str(run.get("status") or "")
        dispatch = DispatchPlan(
            target="system",
            hook="ui_context.handle_pending",
            payload={"action": action, "source": source, "session_id": session_id},
            status="executed" if status in {"waiting", "completed"} else "failed",
            result={
                "action": "routine_start",
                "orchestration_id": pending.get("routine_id"),
                "run_id": run.get("run_id"),
                "run_status": status,
                "no_timer": no_timer,
            },
        )
        return CommandResponse(
            route=route,
            dispatch=dispatch,
            reply_text=(
                "Starting bedtime now."
                if no_timer
                else (
                    f"Timer has been set for {format_duration(int(value))}."
                    if spec.get("confirm_duration") is True
                    else "Timer started."
                )
            ),
            session_id=session_id,
            effective_session_id=session_id,
        )

    if action != "set_alarm":
        state.clear_pending_ui_context(source, session_id)
        dispatch = DispatchPlan(
            target="system",
            hook="ui_context.handle_pending",
            payload={"action": action, "source": source, "session_id": session_id},
            status="failed",
            result={"error": "unsupported_ui_context", "action": action},
        )
        return CommandResponse(
            route=route,
            dispatch=dispatch,
            reply_text="I could not continue that screen action.",
            session_id=session_id,
            effective_session_id=session_id,
        )

    try:
        speech, details = build_alert_response(f"alarm {normalized}", target_source_id, session_id)
    except Exception as exc:
        dispatch = DispatchPlan(
            target="system",
            hook="ui_context.handle_pending",
            payload={"action": "set_alarm", "source": source, "session_id": session_id, "target_source_id": target_source_id, "text": normalized},
            status="pending_clarification",
            result={"action": "set_alarm", "error": "alarm_time_required", "detail": str(exc)},
        )
        return CommandResponse(
            route=route,
            dispatch=dispatch,
            reply_text="I need a time for the alarm.",
            session_id=session_id,
            effective_session_id=session_id,
        )

    state.clear_pending_ui_context(source, session_id)
    dispatch = DispatchPlan(
        target="system",
        hook="ui_context.handle_pending",
        payload={"action": "set_alarm", "source": source, "session_id": session_id, "target_source_id": target_source_id, "text": normalized},
        status="executed",
        result={"action": "set_alarm", "alerts": details, "speech": speech},
    )
    return CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text=speech,
        session_id=session_id,
        effective_session_id=session_id,
    )
