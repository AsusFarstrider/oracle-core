from __future__ import annotations

import json
import logging
from threading import RLock
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .auth import is_authorized
from .cache import CommandCache
from .playback_authority import build_playback_authority_state, interrupt_for_oracle, resume_after_oracle
from .reply_audio import ReplyAudioStateStore


def _log_control_event(
    event: str,
    *,
    command_id: str | None = None,
    action: str | None = None,
    status: str | None = None,
    adapter: str | None = None,
    detail: str | None = None,
    failure_class: str | None = None,
    owning_component: str | None = None,
) -> None:
    logging.info(
        "%s command_id=%s action=%s status=%s adapter=%s failure_class=%s owning_component=%s detail=%s",
        event,
        command_id or "-",
        action or "-",
        status or "-",
        adapter or "-",
        failure_class or "-",
        owning_component or "-",
        detail or "-",
    )


def _error_payload(
    *,
    error: str,
    detail: str,
    failure_class: str,
    owning_component: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "detail": detail,
        "failure_class": failure_class,
        "owning_component": owning_component,
    }


def _invalid_request_result(detail: str) -> Any:
    from .longform import CommandResult

    return CommandResult(
        ok=False,
        state="invalid_request",
        detail=detail,
        failure_class="contract_failure",
        owning_component="satellite.control_service",
    )


def _validate_control_request_payload(payload: dict[str, Any]) -> None:
    extra_keys = sorted(set(payload.keys()) - {"command_id", "action", "args"})
    if extra_keys:
        raise ValueError(f"Unsupported control request fields: {', '.join(extra_keys)}")
    if "command_id" not in payload or "action" not in payload:
        raise ValueError("command_id and action are required")
    if "args" in payload and not isinstance(payload.get("args"), dict):
        raise ValueError("args must be an object when provided")


class ControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "OracleSatelliteControl/0.1"

    def do_GET(self) -> None:
        path, query_params = self._split_path()

        if path == "/health":
            self._write_json(HTTPStatus.OK, self.server.build_health_payload())
            return

        if path == "/health/config":
            response_format = self.server.choose_config_report_format(query_params, self.headers.get("Accept"))
            if response_format == "text":
                self._write_text(HTTPStatus.OK, self.server.render_config_report_text())
                return
            self._write_json(HTTPStatus.OK, self.server.build_config_report_payload())
            return

        if not self.server.authorize(self.headers.get("Authorization")):
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                _error_payload(
                    error="unauthorized",
                    detail="Authorization is required.",
                    failure_class="transport_failure",
                    owning_component="satellite.control_service",
                ),
            )
            return

        if path == "/playback-authority":
            self._write_json(HTTPStatus.OK, self.server.get_playback_authority_state())
            return

        self._write_json(
            HTTPStatus.NOT_FOUND,
            _error_payload(
                error="not_found",
                detail="Unknown control-service endpoint.",
                failure_class="transport_failure",
                owning_component="satellite.control_service",
            ),
        )

    def do_POST(self) -> None:
        if not self.server.authorize(self.headers.get("Authorization")):
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                _error_payload(
                    error="unauthorized",
                    detail="Authorization is required.",
                    failure_class="transport_failure",
                    owning_component="satellite.control_service",
                ),
            )
            return

        if self.path != "/control":
            self._write_json(
                HTTPStatus.NOT_FOUND,
                _error_payload(
                    error="not_found",
                    detail="Unknown control-service endpoint.",
                    failure_class="transport_failure",
                    owning_component="satellite.control_service",
                ),
            )
            return

        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                _error_payload(
                    error="invalid_json",
                    detail=str(exc),
                    failure_class="contract_failure",
                    owning_component="satellite.control_service",
                ),
            )
            return

        try:
            _validate_control_request_payload(payload)
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                _error_payload(
                    error="invalid_request",
                    detail=str(exc),
                    failure_class="contract_failure",
                    owning_component="satellite.control_service",
                ),
            )
            return

        command_id = str(payload.get("command_id", "")).strip()
        action = str(payload.get("action", "")).strip()
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}

        def execute_command() -> dict[str, Any]:
            _log_control_event("control_command_received", command_id=command_id, action=action, status="received")
            _log_control_event(
                "control_command_sent",
                command_id=command_id,
                action=action,
                status="sent",
                adapter=self.server.adapter.__class__.__name__,
            )
            with self.server.runtime_lock:
                return self._dispatch_action(action, args).to_dict(command_id)

        response, cached = self.server.command_cache.get_or_store(
            command_id,
            execute_command,
        )
        if cached:
            _log_control_event(
                "control_command_result",
                command_id=command_id,
                action=action,
                status="cached",
                failure_class=str(response.get("failure_class") or ""),
                owning_component=str(response.get("owning_component") or ""),
            )
            self._write_json(HTTPStatus.OK, response)
            return

        status = HTTPStatus.OK if response.get("ok") is True else HTTPStatus.BAD_REQUEST
        _log_control_event(
            "control_command_result",
            command_id=command_id,
            action=action,
            status=response.get("state"),
            failure_class=str(response.get("failure_class") or ""),
            owning_component=str(response.get("owning_component") or ""),
            detail=str(response.get("detail", "") or ""),
        )
        self._write_json(status, response)

    def log_message(self, fmt: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    def _dispatch_action(self, action: str, args: dict[str, Any]):
        adapter = self.server.adapter
        result_type = self.server.result_type
        if action == "pause":
            return adapter.pause()
        if action == "resume":
            return adapter.resume()
        if action == "stop":
            return adapter.stop()
        if action == "next":
            return adapter.next()
        if action == "previous":
            return adapter.previous()
        if action == "restart":
            return adapter.restart()
        if action == "volume_up":
            return adapter.volume_up()
        if action == "volume_down":
            return adapter.volume_down()
        if action == "set_volume":
            try:
                level = int(args.get("level"))
            except (TypeError, ValueError):
                return _invalid_request_result("set_volume requires integer level")
            return adapter.set_volume(level)
        if action == "play_media":
            plex_key = str(args.get("plex_key", "")).strip()
            media_type = str(args.get("media_type", "")).strip()
            try:
                duration_seconds = float(args.get("duration_seconds") or 0.0)
                queue_position = int(args.get("queue_position") or 0)
                queue_count = int(args.get("queue_count") or 0)
            except (TypeError, ValueError):
                return _invalid_request_result(
                    "play_media duration_seconds, queue_position, and queue_count must be numeric when provided"
                )
            queue_tracks = args.get("queue_tracks")
            if queue_tracks is not None and not isinstance(queue_tracks, list):
                return _invalid_request_result("play_media queue_tracks must be a list when provided")
            if not plex_key or not media_type:
                return _invalid_request_result("play_media requires media_type and plex_key")
            return adapter.play_media(
                media_type=media_type,
                plex_key=plex_key,
                parent_key=str(args.get("parent_key", "")).strip(),
                rating_key=str(args.get("rating_key", "")).strip(),
                title=str(args.get("title", "")).strip(),
                artist=str(args.get("artist", "")).strip(),
                album=str(args.get("album", "")).strip(),
                backend_hint=str(args.get("backend_hint", "")).strip(),
                duration_seconds=duration_seconds,
                queue_id=str(args.get("queue_id", "")).strip(),
                queue_position=queue_position,
                queue_count=queue_count,
                collection_title=str(args.get("collection_title", "")).strip(),
                collection_type=str(args.get("collection_type", "")).strip(),
                queue_tracks=queue_tracks if isinstance(queue_tracks, list) else None,
            )
        if action == "play_longform_audio":
            playback_id = str(args.get("playback_id", "")).strip()
            session_id = str(args.get("session_id", "")).strip()
            title = str(args.get("title", "")).strip()
            author = str(args.get("author", "")).strip()
            try:
                duration_seconds = float(args.get("duration_seconds") or 0)
                start_position_seconds = float(args.get("start_position_seconds") or 0)
            except (TypeError, ValueError):
                return _invalid_request_result(
                    "play_longform_audio requires numeric duration_seconds and start_position_seconds"
                )
            tracks = args.get("tracks")
            chapters = args.get("chapters")
            start_paused = bool(args.get("start_paused"))
            if not playback_id or not session_id or not isinstance(tracks, list) or not tracks:
                return _invalid_request_result("play_longform_audio requires playback_id, session_id, and tracks")
            return adapter.play_longform_audio(
                playback_id=playback_id,
                session_id=session_id,
                title=title,
                author=author,
                duration_seconds=duration_seconds,
                start_position_seconds=start_position_seconds,
                start_paused=start_paused,
                tracks=tracks,
                chapters=chapters if isinstance(chapters, list) else None,
            )
        if action == "pause_longform_audio":
            return adapter.pause_longform_audio()
        if action == "resume_longform_audio":
            return adapter.resume_longform_audio()
        if action == "stop_longform_audio":
            return adapter.stop_longform_audio()
        if action == "seek_longform_audio":
            try:
                position_seconds = float(args.get("position_seconds"))
            except (TypeError, ValueError):
                return _invalid_request_result("seek_longform_audio requires numeric position_seconds")
            return adapter.seek_longform_audio(position_seconds)
        if action == "get_longform_state":
            try:
                payload = adapter.get_longform_state()
            except RuntimeError as exc:
                return result_type(
                    ok=False,
                    state="failed",
                    detail=str(exc),
                    failure_class="control_service_failure",
                    owning_component="satellite.control_service",
                )
            return result_type(ok=True, state="accepted", payload=payload)
        if action == "stop_reply_audio":
            return result_type(ok=True, state="accepted", payload=self.server.stop_reply_audio())
        if action == "begin_reply_audio":
            kind = str(args.get("kind", "tts")).strip() or "tts"
            correlation_id = str(args.get("correlation_id", "")).strip()
            return result_type(
                ok=True,
                state="accepted",
                payload=self.server.begin_reply_audio(kind=kind, correlation_id=correlation_id),
            )
        if action == "finalize_reply_audio":
            session_id = str(args.get("session_id", "")).strip()
            correlation_id = str(args.get("correlation_id", "")).strip()
            final_state = str(args.get("final_state", "")).strip()
            reason = str(args.get("reason", "")).strip()
            if not session_id or not final_state:
                return _invalid_request_result("finalize_reply_audio requires session_id and final_state")
            return result_type(
                ok=True,
                state="accepted",
                payload=self.server.finalize_reply_audio(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    final_state=final_state,
                    reason=reason,
                ),
            )
        if action == "interrupt_for_oracle":
            return result_type(ok=True, state="accepted", payload=self.server.interrupt_for_oracle())
        if action == "resume_after_oracle":
            interrupted_sessions = args.get("interrupted_sessions")
            if interrupted_sessions is not None and not isinstance(interrupted_sessions, list):
                return _invalid_request_result("resume_after_oracle requires interrupted_sessions as a list")
            return result_type(
                ok=True,
                state="accepted",
                payload=self.server.resume_after_oracle(
                    interrupted_sessions if isinstance(interrupted_sessions, list) else []
                ),
            )
        return result_type(
            ok=False,
            state="unsupported",
            detail=f"Unsupported action {action}",
            failure_class="contract_failure",
            owning_component="satellite.control_service",
        )

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(content_length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def _split_path(self) -> tuple[str, dict[str, str]]:
        parsed = urlsplit(self.path)
        query = {
            key: values[-1]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if values
        }
        return parsed.path, query

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: HTTPStatus, payload: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ControlServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[ControlRequestHandler],
        *,
        api_key: str,
        adapter: Any,
        reply_audio_state_path: str,
        reply_audio_stop_path: str,
        result_type: type[Any],
        build_config_report_payload: Any,
        render_config_report_text: Any,
        choose_config_report_format: Any,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.api_key = api_key
        self.adapter = adapter
        self.reply_audio = ReplyAudioStateStore(reply_audio_state_path, reply_audio_stop_path)
        self.command_cache = CommandCache()
        self.runtime_lock = RLock()
        self.result_type = result_type
        self._build_config_report_payload = build_config_report_payload
        self._render_config_report_text = render_config_report_text
        self.choose_config_report_format = choose_config_report_format

    def authorize(self, header: str | None) -> bool:
        return is_authorized(self.api_key, header)

    def build_health_payload(self) -> dict[str, Any]:
        with self.runtime_lock:
            return {
                "ok": True,
                "service": "oracle-satellite-control",
                "adapter": self.adapter.health(),
            }

    def get_reply_audio_state(self) -> dict[str, Any]:
        with self.runtime_lock:
            return self.reply_audio.get_state()

    def stop_reply_audio(self) -> dict[str, Any]:
        with self.runtime_lock:
            return self.reply_audio.request_stop()

    def begin_reply_audio(self, *, kind: str, correlation_id: str = "") -> dict[str, Any]:
        with self.runtime_lock:
            return self.reply_audio.begin_session(kind=kind, correlation_id=correlation_id)

    def finalize_reply_audio(
        self,
        *,
        session_id: str,
        correlation_id: str = "",
        final_state: str,
        reason: str = "",
    ) -> dict[str, Any]:
        with self.runtime_lock:
            return self.reply_audio.finalize_session(
                session_id=session_id,
                correlation_id=correlation_id,
                final_state=final_state,
                reason=reason,
            )

    def get_playback_authority_state(self) -> dict[str, Any]:
        with self.runtime_lock:
            return build_playback_authority_state(adapter=self.adapter, reply_audio=self.reply_audio)

    def interrupt_for_oracle(self) -> dict[str, Any]:
        with self.runtime_lock:
            return interrupt_for_oracle(adapter=self.adapter, reply_audio=self.reply_audio)

    def resume_after_oracle(self, interrupted_sessions: list[dict[str, Any]]) -> dict[str, Any]:
        with self.runtime_lock:
            return resume_after_oracle(adapter=self.adapter, interrupted_sessions=interrupted_sessions)

    def build_config_report_payload(self) -> dict[str, Any]:
        return self._build_config_report_payload()

    def render_config_report_text(self) -> str:
        return self._render_config_report_text()
