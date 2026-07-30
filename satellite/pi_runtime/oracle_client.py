from __future__ import annotations

import http.client
import json
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from .models import CommandOutcome, WakeArbitrationDecision
from .replies import extract_spoken_reply


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _request_headers(
    *,
    credential: str | None = None,
    correlation_id: str | None = None,
) -> Dict[str, str] | None:
    headers: Dict[str, str] = {}
    clean_credential = str(credential or "").strip()
    clean_correlation_id = str(correlation_id or "").strip()
    if clean_credential:
        headers["Authorization"] = f"Bearer {clean_credential}"
    if clean_correlation_id:
        headers["X-Oracle-Correlation-Id"] = clean_correlation_id
    return headers or None


def _header_kwargs(*, credential: str | None = None) -> Dict[str, Any]:
    headers = _request_headers(credential=credential)
    return {"headers": headers} if headers is not None else {}


def send_stt(
    oracle_url: str,
    wav_bytes: bytes,
    *,
    correlation_id: str | None = None,
    source: str | None = None,
    credential: str | None = None,
    on_upload_complete: Optional[Callable[[], None]] = None,
    on_upload_complete_error: Optional[Callable[[Exception], None]] = None,
) -> str:
    parsed = urlsplit(f"{oracle_url.rstrip('/')}/stt")
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"Unsupported STT URL scheme: {parsed.scheme}")

    boundary = f"oracle-{uuid.uuid4().hex}"
    body_parts = []
    clean_source = str(source or "").strip()
    if clean_source:
        body_parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="source"\r\n\r\n'
                f"{clean_source}\r\n"
            ).encode("utf-8")
        )
    body_parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="speech.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8")
        + wav_bytes
        + b"\r\n"
    )
    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(body_parts)

    clean_correlation_id = str(correlation_id or "").strip()

    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_cls(parsed.hostname, parsed.port, timeout=120)
    try:
        connection.putrequest("POST", parsed.path or "/stt")
        headers = _request_headers(
            credential=credential,
            correlation_id=clean_correlation_id,
        )
        for header, value in (headers or {}).items():
            connection.putheader(header, value)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        connection.send(body)
        if on_upload_complete is not None:
            try:
                on_upload_complete()
            except Exception as exc:
                if on_upload_complete_error is not None:
                    on_upload_complete_error(exc)
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()

    if response.status >= 400:
        detail = payload.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"STT request failed with HTTP {response.status}")

    data = json.loads(payload.decode("utf-8"))
    text = normalize_text(str(data.get("text", "")))
    if not text:
        raise RuntimeError("Oracle returned an empty transcript")
    return text


def send_command(
    oracle_url: str,
    source: str,
    transcript: str,
    session_id: str,
    *,
    correlation_id: str | None = None,
    credential: str | None = None,
) -> CommandOutcome:
    request_kwargs: Dict[str, Any] = {
        "json": {"text": transcript, "source": source, "session_id": session_id},
        "timeout": 120,
    }
    headers = _request_headers(credential=credential, correlation_id=correlation_id)
    if headers is not None:
        request_kwargs["headers"] = headers
    response = requests.post(f"{oracle_url.rstrip('/')}/command", **request_kwargs)
    response.raise_for_status()
    data = response.json()
    spoken_reply = extract_spoken_reply(data)
    return CommandOutcome(transcript=transcript, spoken_reply=spoken_reply, raw_response=data)


def submit_wake_claim(
    oracle_url: str,
    *,
    satellite_id: str,
    room_id: str | None = None,
    profile: str | None = None,
    timestamp: str | None = None,
    wake_confidence: float | None = None,
    audio_level: float | None = None,
    correlation_id: str | None = None,
    credential: str | None = None,
    timeout: float = 5.0,
) -> WakeArbitrationDecision:
    body: Dict[str, Any] = {"satellite_id": satellite_id}
    for key, value in {
        "room_id": room_id,
        "profile": profile,
        "timestamp": timestamp,
        "wake_confidence": wake_confidence,
        "audio_level": audio_level,
        "correlation_id": correlation_id,
    }.items():
        if value is not None and str(value).strip() != "":
            body[key] = value

    request_kwargs: Dict[str, Any] = {
        "json": body,
        "timeout": timeout,
    }
    headers = _request_headers(credential=credential, correlation_id=correlation_id)
    if headers is not None:
        request_kwargs["headers"] = headers

    response = requests.post(f"{oracle_url.rstrip('/')}/api/satellite/wake", **request_kwargs)
    response.raise_for_status()
    data = response.json()
    participants = data.get("participants")
    return WakeArbitrationDecision(
        interaction_id=str(data.get("interaction_id") or ""),
        satellite_id=str(data.get("satellite_id") or satellite_id),
        winner_satellite_id=str(data.get("winner_satellite_id") or ""),
        decision=str(data.get("decision") or ""),
        reason=str(data.get("reason") or ""),
        participants=[str(item) for item in participants] if isinstance(participants, list) else [],
        window_ms=int(data.get("window_ms") or 0),
        room_id=str(data.get("room_id") or "").strip() or None,
        profile=str(data.get("profile") or "").strip() or None,
        raw_response=data,
    )


def report_satellite_activity(
    oracle_url: str,
    *,
    source_id: str,
    event_type: str | None = None,
    status: str | None = None,
    correlation_id: str | None = None,
    credential: str | None = None,
    payload: Dict[str, Any] | None = None,
    snapshot: Dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> None:
    body: Dict[str, Any] = {
        "source_id": source_id,
        "payload": payload or {},
        "snapshot": snapshot or {},
    }
    if event_type:
        body["event_type"] = event_type
    if status:
        body["status"] = status
    clean_correlation_id = str(correlation_id or "").strip()
    headers = _request_headers(credential=credential, correlation_id=clean_correlation_id)
    if clean_correlation_id:
        body["correlation_id"] = clean_correlation_id
    try:
        response = requests.post(
            f"{oracle_url.rstrip('/')}/api/satellite/activity",
            json=body,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
    except Exception:
        return


def fetch_pending_alerts(
    oracle_url: str,
    source: str,
    *,
    credential: str | None = None,
) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{oracle_url.rstrip('/')}/alerts/pending",
        params={"source": source},
        timeout=30,
        **_header_kwargs(credential=credential),
    )
    response.raise_for_status()
    data = response.json()
    alerts = data.get("alerts")
    return alerts if isinstance(alerts, list) else []


def fetch_command_events(
    oracle_url: str,
    *,
    source: str,
    session_id: str,
    after_event_id: int = 0,
    timeout: float = 2.0,
    credential: str | None = None,
) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{oracle_url.rstrip('/')}/api/voice/command-events",
        params={
            "source": source,
            "session_id": session_id,
            "after_event_id": max(0, int(after_event_id or 0)),
        },
        timeout=timeout,
        **_header_kwargs(credential=credential),
    )
    response.raise_for_status()
    data = response.json()
    events = data.get("events")
    return events if isinstance(events, list) else []


def send_silent_audiobook_stop(
    oracle_url: str,
    source: str,
    alert_id: str,
    *,
    credential: str | None = None,
) -> Dict[str, Any]:
    response = requests.post(
        f"{oracle_url.rstrip('/')}/command",
        json={
            "text": "stop audiobook",
            "source": source,
            "session_id": f"{source}-sleep-timer-{alert_id}",
        },
        timeout=120,
        **_header_kwargs(credential=credential),
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise requests.RequestException("Sleep timer Brain stop returned invalid JSON") from exc
    dispatch = payload.get("dispatch") if isinstance(payload, dict) else None
    result = dispatch.get("result") if isinstance(dispatch, dict) else None
    if (
        not isinstance(dispatch, dict)
        or str(dispatch.get("status") or "").strip().lower() != "executed"
        or not isinstance(result, dict)
        or str(result.get("action") or "").strip().lower() != "stop"
    ):
        raise requests.RequestException("Sleep timer Brain stop did not execute")
    return payload


def request_tts(
    oracle_url: str,
    text: str,
    *,
    credential: str | None = None,
) -> bytes:
    response = requests.post(
        f"{oracle_url.rstrip('/')}/tts",
        json={"text": text},
        timeout=120,
        **_header_kwargs(credential=credential),
    )
    response.raise_for_status()
    return response.content
