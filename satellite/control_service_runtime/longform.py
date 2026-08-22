from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CommandResult:
    ok: bool
    state: str
    detail: str = ""
    payload: Optional[dict[str, Any]] = None
    failure_class: str = ""
    owning_component: str = ""

    def to_dict(self, command_id: str) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "command_id": command_id,
            "state": self.state,
        }
        if self.detail:
            data["detail"] = self.detail
        if self.failure_class:
            data["failure_class"] = self.failure_class
        if self.owning_component:
            data["owning_component"] = self.owning_component
        if self.payload:
            data.update(self.payload)
        return data


class LongformShellController:
    def __init__(self, args: argparse.Namespace) -> None:
        self._commands = {
            "play_longform_audio": args.play_longform_audio_cmd,
            "pause_longform_audio": args.pause_longform_audio_cmd,
            "resume_longform_audio": args.resume_longform_audio_cmd,
            "stop_longform_audio": args.stop_longform_audio_cmd,
            "seek_longform_audio": args.seek_longform_audio_cmd,
            "get_longform_state": args.longform_state_cmd,
        }
        self._startup_poll_attempts = 6
        self._startup_poll_interval_seconds = 0.5
        self._startup_retry_attempts = 1
        self._state_cache_ttl_seconds = 0.5
        self._state_cache_payload: Optional[dict[str, Any]] = None
        self._state_cache_checked_at = 0.0

    def health(self) -> dict[str, Any]:
        available_actions = sorted(action for action, cmd in self._commands.items() if cmd)
        return {"available_actions": available_actions}

    def play_longform_audio(
        self,
        *,
        playback_id: str,
        session_id: str,
        title: str,
        author: str,
        duration_seconds: float,
        start_position_seconds: float,
        start_paused: bool = False,
        tracks: list[dict[str, Any]],
        chapters: Optional[list[dict[str, Any]]] = None,
    ) -> CommandResult:
        self._invalidate_state_cache()
        command = self._commands.get("play_longform_audio")
        if not command:
            return CommandResult(
                ok=False,
                state="unsupported",
                detail="No local command is configured for action play_longform_audio",
            )

        manifest = {
            "playback_id": playback_id,
            "session_id": session_id,
            "title": title,
            "author": author,
            "duration_seconds": duration_seconds,
            "start_position_seconds": start_position_seconds,
            "start_paused": bool(start_paused),
            "tracks": tracks,
            "chapters": chapters or [],
        }
        fd, manifest_path = tempfile.mkstemp(prefix="oracle-longform-", suffix=".json")
        try:
            os.write(fd, json.dumps(manifest).encode("utf-8"))
        finally:
            os.close(fd)
        context = {
            "manifest_path": manifest_path,
            "playback_id": playback_id,
            "session_id": session_id,
            "title": title,
            "author": author,
            "duration_seconds": duration_seconds,
            "start_position_seconds": start_position_seconds,
        }
        attempts = self._startup_retry_attempts + 1
        latest_result = CommandResult(ok=False, state="failed", detail="play_longform_audio did not start")
        for attempt in range(attempts):
            launch_result = self._run_command(command, context)
            latest_result = self._confirm_longform_started(
                launch_result,
                expected_playback_id=playback_id,
                action="play_longform_audio",
            )
            if latest_result.ok:
                return latest_result
            if attempt >= attempts - 1 or not self._should_retry_startup_failure(latest_result):
                return latest_result
            time.sleep(self._startup_poll_interval_seconds)
        return latest_result

    def pause_longform_audio(self) -> CommandResult:
        self._invalidate_state_cache()
        return self._run_simple("pause_longform_audio")

    def resume_longform_audio(self) -> CommandResult:
        self._invalidate_state_cache()
        result = self._run_simple("resume_longform_audio")
        return self._confirm_longform_started(result, action="resume_longform_audio")

    def stop_longform_audio(self) -> CommandResult:
        self._invalidate_state_cache()
        return self._run_simple("stop_longform_audio")

    def seek_longform_audio(self, position_seconds: float) -> CommandResult:
        self._invalidate_state_cache()
        result = self._run_command(
            self._commands.get("seek_longform_audio"),
            {"position_seconds": position_seconds},
            missing_detail="No local command is configured for action seek_longform_audio",
        )
        return self._confirm_longform_started(result, action="seek_longform_audio")

    def get_longform_state(self, *, use_cache: bool = True) -> dict[str, Any]:
        command = self._commands.get("get_longform_state")
        if not command:
            return {"ok": True, "playing": False, "state": "stopped"}
        cached_payload = self._get_cached_state() if use_cache else None
        if cached_payload is not None:
            return cached_payload
        completed = self._run_command(command, {})
        if not completed.ok:
            raise RuntimeError(completed.detail or "longform state command failed")
        payload = completed.payload or {}
        payload.setdefault("ok", True)
        payload.setdefault("playing", payload.get("state") in {"playing", "paused", "buffering"})
        self._store_cached_state(payload)
        return payload

    def _run_simple(self, action: str) -> CommandResult:
        command = self._commands.get(action)
        return self._run_command(
            command,
            {},
            missing_detail=f"No local command is configured for action {action}",
        )

    def _confirm_longform_started(
        self,
        result: CommandResult,
        *,
        action: str,
        expected_playback_id: Optional[str] = None,
    ) -> CommandResult:
        if not result.ok:
            return result
        latest_state: dict[str, Any] = result.payload or {}
        for attempt in range(self._startup_poll_attempts):
            if attempt:
                time.sleep(self._startup_poll_interval_seconds)
            try:
                latest_state = self.get_longform_state(use_cache=False)
            except RuntimeError as exc:
                return CommandResult(ok=False, state="failed", detail=str(exc))
            state = str(latest_state.get("state", "")).strip()
            if state not in {"playing", "paused", "buffering"}:
                continue
            if expected_playback_id:
                playback_id = str(latest_state.get("playback_id", "")).strip()
                if playback_id and playback_id != expected_playback_id:
                    continue
            return CommandResult(ok=True, state="accepted", payload=latest_state)
        detail = f"{action} did not reach a playable long-form state"
        state = str(latest_state.get("state", "")).strip()
        if state:
            detail = f"{detail} (final state: {state})"
        return CommandResult(ok=False, state="failed", detail=detail, payload=latest_state or None)

    def _should_retry_startup_failure(self, result: CommandResult) -> bool:
        if result.ok:
            return False
        payload = result.payload or {}
        if not isinstance(payload, dict):
            return False
        state = str(payload.get("state", "")).strip().lower()
        return state == "stopped"

    def _run_command(
        self,
        template: Optional[str],
        context: dict[str, Any],
        *,
        missing_detail: str = "No local command is configured",
    ) -> CommandResult:
        if not template:
            return CommandResult(ok=False, state="unsupported", detail=missing_detail)
        try:
            command = template.format(**context)
        except KeyError as exc:
            return CommandResult(
                ok=False,
                state="invalid_configuration",
                detail=f"Missing command template field: {exc.args[0]}",
            )

        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            return CommandResult(
                ok=False,
                state="failed",
                detail=stderr or stdout or f"Command exited with {completed.returncode}",
            )
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = {"stdout": stdout}
        else:
            payload = None
        return CommandResult(ok=True, state="accepted", payload=payload)

    def _get_cached_state(self) -> Optional[dict[str, Any]]:
        if self._state_cache_payload is None:
            return None
        if (time.monotonic() - self._state_cache_checked_at) > self._state_cache_ttl_seconds:
            return None
        return dict(self._state_cache_payload)

    def _store_cached_state(self, payload: dict[str, Any]) -> None:
        self._state_cache_payload = dict(payload)
        self._state_cache_checked_at = time.monotonic()

    def _invalidate_state_cache(self) -> None:
        self._state_cache_payload = None
        self._state_cache_checked_at = 0.0
