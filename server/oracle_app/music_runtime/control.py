from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from oracle_app.config import get_satellite_control_target


_LONGFORM_START_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class SatelliteControlTarget:
    base_url: str
    credential: str
    timeout_seconds: float


class ControlPlaneError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        failure_class: str,
        owning_component: str,
        error_code: str,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.failure_class = failure_class
        self.owning_component = owning_component
        self.error_code = error_code


def build_control_plane_failure(
    *,
    action: str,
    exc: RuntimeError,
    error: str = "satellite_command_failed",
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "error": error,
        "detail": str(exc),
    }
    if isinstance(exc, ControlPlaneError):
        payload["failure_class"] = exc.failure_class
        payload["owning_component"] = exc.owning_component
        payload["control_error"] = exc.error_code
    payload.update(extra)
    return payload


def _parse_error_payload(detail: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(detail)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_control_command_response(action: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ControlPlaneError(
            "Satellite control returned a non-object response.",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        )
    if not isinstance(payload.get("ok"), bool):
        raise ControlPlaneError(
            "Satellite control response omitted boolean ok.",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        )
    if not str(payload.get("command_id", "")).strip():
        raise ControlPlaneError(
            "Satellite control response omitted command_id.",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        )
    if not str(payload.get("state", "")).strip():
        raise ControlPlaneError(
            "Satellite control response omitted state.",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        )
    if action == "interrupt_for_oracle":
        interrupted_any = bool(payload.get("interrupted_any"))
        sessions = payload.get("interrupted_sessions")
        if interrupted_any and not isinstance(sessions, list):
            raise ControlPlaneError(
                "interrupt_for_oracle reported interrupted_any without interrupted_sessions.",
                failure_class="authority_mismatch",
                owning_component="satellite.playback_authority",
                error_code="authority_interrupt_result_invalid",
            )
    return payload


def _validate_authority_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ControlPlaneError(
            "Satellite playback authority returned a non-object response.",
            failure_class="contract_failure",
            owning_component="satellite.playback_authority",
            error_code="authority_response_invalid",
        )
    sessions = payload.get("sessions")
    active_sessions = payload.get("active_sessions")
    if sessions is not None and not isinstance(sessions, list):
        raise ControlPlaneError(
            "Satellite playback authority sessions must be a list.",
            failure_class="contract_failure",
            owning_component="satellite.playback_authority",
            error_code="authority_response_invalid",
        )
    if active_sessions is not None and not isinstance(active_sessions, list):
        raise ControlPlaneError(
            "Satellite playback authority active_sessions must be a list.",
            failure_class="contract_failure",
            owning_component="satellite.playback_authority",
            error_code="authority_response_invalid",
        )
    return payload


def execute_satellite_command(
    source: str | None,
    action: str,
    args: dict[str, Any] | None = None,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any]:
    target = control_target or _legacy_control_target(source)
    endpoint = f"{target.base_url}/control"
    payload = {
        "command_id": uuid.uuid4().hex,
        "action": action,
        "args": args or {},
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {target.credential}",
        },
        method="POST",
    )
    try:
        timeout_seconds = target.timeout_seconds
        if action == "play_longform_audio":
            timeout_seconds = max(timeout_seconds, _LONGFORM_START_TIMEOUT_SECONDS)
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return _validate_control_command_response(action, payload)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        parsed = _parse_error_payload(detail)
        if parsed is not None:
            raise ControlPlaneError(
                str(parsed.get("detail") or parsed.get("error") or f"Satellite control returned HTTP {exc.code}"),
                failure_class=str(parsed.get("failure_class") or "control_service_failure"),
                owning_component=str(parsed.get("owning_component") or "satellite.control_service"),
                error_code=str(parsed.get("error") or "control_http_error"),
            ) from exc
        raise ControlPlaneError(
            detail or f"Satellite control returned HTTP {exc.code}",
            failure_class="control_service_failure",
            owning_component="satellite.control_service",
            error_code="control_http_error",
        ) from exc
    except error.URLError as exc:
        raise ControlPlaneError(
            str(exc.reason),
            failure_class="transport_failure",
            owning_component="brain.control_plane_client",
            error_code="control_unreachable",
        ) from exc
    except TimeoutError as exc:
        raise ControlPlaneError(
            str(exc) or "Satellite control request timed out",
            failure_class="transport_failure",
            owning_component="brain.control_plane_client",
            error_code="control_timeout",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ControlPlaneError(
            "Satellite control returned invalid JSON",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        ) from exc


def fetch_satellite_playback_authority(
    source: str | None,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any]:
    return _fetch_satellite_control_json(
        source,
        "/playback-authority",
        control_target=control_target,
    )


def fetch_satellite_music_session(
    source: str | None,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any] | None:
    authority = fetch_satellite_playback_authority(source, control_target=control_target)
    if not isinstance(authority, dict):
        return None
    return _find_authority_session(authority, backend_type=None, media_kind="music")


def fetch_satellite_audiobook_session(
    source: str | None,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any] | None:
    authority = fetch_satellite_playback_authority(source, control_target=control_target)
    if not isinstance(authority, dict):
        return None
    return _find_authority_session(authority, backend_type="oracle_audiobook", media_kind="audiobook")


def fetch_satellite_audiobook_context_session(source: str | None) -> dict[str, Any] | None:
    authority = fetch_satellite_playback_authority(source)
    if not isinstance(authority, dict):
        return None
    session = _find_authority_session(authority, backend_type="oracle_audiobook", media_kind="audiobook")
    if isinstance(session, dict):
        return session
    sessions = authority.get("sessions")
    if not isinstance(sessions, list):
        return None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        if str(session.get("backend_type", "")).strip().lower() != "oracle_audiobook":
            continue
        if str(session.get("media_kind", "")).strip().lower() != "audiobook":
            continue
        return session
    return None


def fetch_satellite_reply_audio_session(
    source: str | None,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any] | None:
    authority = fetch_satellite_playback_authority(source, control_target=control_target)
    if not isinstance(authority, dict):
        return None
    return _find_authority_session(authority, backend_type="reply_audio", media_kind="reply")


def _fetch_satellite_control_json(
    source: str | None,
    path: str,
    *,
    control_target: SatelliteControlTarget | None = None,
) -> dict[str, Any]:
    target = control_target or _legacy_control_target(source)
    endpoint = f"{target.base_url}{path}"
    req = request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {target.credential}"},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=target.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return _validate_authority_response(payload)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        parsed = _parse_error_payload(detail)
        if parsed is not None:
            raise ControlPlaneError(
                str(parsed.get("detail") or parsed.get("error") or f"Satellite control returned HTTP {exc.code}"),
                failure_class=str(parsed.get("failure_class") or "control_service_failure"),
                owning_component=str(parsed.get("owning_component") or "satellite.control_service"),
                error_code=str(parsed.get("error") or "control_http_error"),
            ) from exc
        raise ControlPlaneError(
            detail or f"Satellite control returned HTTP {exc.code}",
            failure_class="control_service_failure",
            owning_component="satellite.control_service",
            error_code="control_http_error",
        ) from exc
    except error.URLError as exc:
        raise ControlPlaneError(
            str(exc.reason),
            failure_class="transport_failure",
            owning_component="brain.control_plane_client",
            error_code="control_unreachable",
        ) from exc
    except TimeoutError as exc:
        raise ControlPlaneError(
            str(exc) or "Satellite control request timed out",
            failure_class="transport_failure",
            owning_component="brain.control_plane_client",
            error_code="control_timeout",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ControlPlaneError(
            "Satellite control returned invalid JSON",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
            error_code="control_response_invalid",
        ) from exc


def _legacy_control_target(source: str | None) -> SatelliteControlTarget:
    target = get_satellite_control_target(source)
    return SatelliteControlTarget(
        base_url=str(target["base_url"]),
        credential=str(target["api_key"]),
        timeout_seconds=int(target["timeout_seconds"]),
    )


def _find_authority_session(
    authority: dict[str, Any],
    *,
    backend_type: str | None,
    media_kind: str,
) -> dict[str, Any] | None:
    sessions = authority.get("active_sessions")
    if not isinstance(sessions, list):
        return None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        if backend_type is not None and str(session.get("backend_type", "")).strip().lower() != backend_type:
            continue
        if str(session.get("media_kind", "")).strip().lower() != media_kind:
            continue
        return session
    return None
