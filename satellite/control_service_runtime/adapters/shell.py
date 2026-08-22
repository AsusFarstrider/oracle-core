from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from satellite.control_service_runtime.longform import CommandResult, LongformShellController


class ShellPlexampAdapter:
    def __init__(self, args: argparse.Namespace) -> None:
        self._longform = LongformShellController(args)
        self._commands = {
            "pause": args.pause_cmd,
            "resume": args.resume_cmd,
            "stop": args.stop_cmd,
            "next": args.next_cmd,
            "previous": args.previous_cmd,
            "restart": args.restart_cmd,
            "set_volume": args.set_volume_cmd,
            "play_media": args.play_media_cmd,
            "now_playing": args.now_playing_cmd,
        }

    def health(self) -> dict[str, Any]:
        available_actions = sorted(action for action, cmd in self._commands.items() if cmd)
        return {
            "adapter": "shell",
            "available_actions": available_actions,
            "longform": self._longform.health(),
        }

    def pause(self) -> CommandResult:
        return self._run_simple("pause")

    def resume(self) -> CommandResult:
        return self._run_simple("resume")

    def stop(self) -> CommandResult:
        return self._run_simple("stop")

    def next(self) -> CommandResult:
        return self._run_simple("next")

    def previous(self) -> CommandResult:
        return self._run_simple("previous")

    def restart(self) -> CommandResult:
        return self._run_simple("restart")

    def set_volume(self, level: int) -> CommandResult:
        result = self._run("set_volume", {"level": level})
        if result.ok:
            result.payload = {**(result.payload or {}), "volume_level": level}
        return result

    def volume_up(self) -> CommandResult:
        return CommandResult(ok=False, state="unsupported", detail="volume_up is unsupported for the shell adapter")

    def volume_down(self) -> CommandResult:
        return CommandResult(ok=False, state="unsupported", detail="volume_down is unsupported for the shell adapter")

    def get_output_volume(self) -> int | None:
        return None

    def play_media(self, media_type: str, plex_key: str, title: str = "", artist: str = "", album: str = "", **_: Any) -> CommandResult:
        return self._run(
            "play_media",
            {
                "media_type": media_type,
                "plex_key": plex_key,
                "title": title,
                "artist": artist,
                "album": album,
            },
        )

    def get_now_playing(self) -> dict[str, Any]:
        command = self._commands.get("now_playing")
        if not command:
            return {"ok": True, "playing": False}
        completed = self._run_command(command, {})
        if not completed.ok:
            raise RuntimeError(completed.detail or "now_playing command failed")
        payload = completed.payload or {}
        payload.setdefault("ok", True)
        return payload

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
        chapters: list[dict[str, Any]] | None = None,
    ) -> CommandResult:
        interrupt_result = self._stop_music_for_longform_start()
        if interrupt_result is not None:
            return interrupt_result
        return self._longform.play_longform_audio(
            playback_id=playback_id,
            session_id=session_id,
            title=title,
            author=author,
            duration_seconds=duration_seconds,
            start_position_seconds=start_position_seconds,
            start_paused=start_paused,
            tracks=tracks,
            chapters=chapters,
        )

    def pause_longform_audio(self) -> CommandResult:
        return self._longform.pause_longform_audio()

    def resume_longform_audio(self) -> CommandResult:
        return self._longform.resume_longform_audio()

    def stop_longform_audio(self) -> CommandResult:
        return self._longform.stop_longform_audio()

    def seek_longform_audio(self, position_seconds: float) -> CommandResult:
        return self._longform.seek_longform_audio(position_seconds)

    def get_longform_state(self) -> dict[str, Any]:
        return self._longform.get_longform_state()

    def _run_simple(self, action: str) -> CommandResult:
        return self._run(action, {})

    def _run(self, action: str, context: dict[str, Any]) -> CommandResult:
        command = self._commands.get(action)
        if not command:
            return CommandResult(
                ok=False,
                state="unsupported",
                detail=f"No local command is configured for action {action}",
            )
        return self._run_command(command, context)

    def _run_command(self, template: str, context: dict[str, Any]) -> CommandResult:
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

    def _stop_music_for_longform_start(self) -> CommandResult | None:
        try:
            now_playing = self.get_now_playing()
        except RuntimeError as exc:
            return CommandResult(ok=False, state="failed", detail=str(exc))
        if not self._music_active_for_longform_start(now_playing):
            return None
        result = self.stop()
        if result.ok:
            return None
        return CommandResult(
            ok=False,
            state="failed",
            detail=result.detail or "Failed to stop active music before starting long-form audio",
            payload=result.payload,
        )

    def _music_active_for_longform_start(self, now_playing: dict[str, Any] | None) -> bool:
        if not isinstance(now_playing, dict):
            return False
        current_state = str(now_playing.get("state", "")).strip().lower()
        return bool(now_playing.get("playing")) or current_state in {"playing", "paused", "buffering", "starting", "stopping"}
