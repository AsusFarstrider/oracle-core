from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .longform import CommandResult


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NATIVE_MUSIC_PLAYER = REPO_ROOT / "satellite" / "native_music_player.py"


class NativeMusicController:
    def __init__(self, args: argparse.Namespace) -> None:
        self._player_bin = str(getattr(args, "oracle_native_music_player_bin", "") or "auto").strip() or "auto"
        self._enabled = bool(getattr(args, "supports_oracle_native_music", False))

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "player_bin": self._player_bin,
        }

    def is_enabled(self) -> bool:
        return self._enabled

    def play_track(
        self,
        *,
        stream_url: str,
        track_id: str,
        media_type: str,
        title: str,
        artist: str,
        album: str,
        queue_id: str = "",
        queue_position: int = 0,
        queue_count: int = 0,
        collection_title: str = "",
        collection_type: str = "",
        queue_tracks: list[dict[str, Any]] | None = None,
        duration_seconds: float,
    ) -> CommandResult:
        if not self._enabled:
            return CommandResult(ok=False, state="unsupported", detail="Oracle-native music is disabled")
        return self._run(
            [
                "play",
                "--url",
                stream_url,
                "--track-id",
                track_id,
                "--media-type",
                media_type,
                "--title",
                title,
                "--artist",
                artist,
                "--album",
                album,
                "--queue-id",
                queue_id,
                "--queue-position",
                str(max(0, queue_position)),
                "--queue-count",
                str(max(0, queue_count)),
                "--collection-title",
                collection_title,
                "--collection-type",
                collection_type,
                "--queue-tracks-json",
                json.dumps(queue_tracks or []),
                "--duration-seconds",
                f"{max(0.0, duration_seconds):.3f}",
                "--player-bin",
                self._player_bin,
            ]
        )

    def pause(self) -> CommandResult:
        return self._run(["pause"])

    def resume(self) -> CommandResult:
        return self._run(["resume", "--player-bin", self._player_bin])

    def stop(self) -> CommandResult:
        return self._run(["stop"])

    def restart(self) -> CommandResult:
        return self._run(["restart", "--player-bin", self._player_bin])

    def state(self) -> dict[str, Any]:
        result = self._run(["state"])
        if not result.ok:
            raise RuntimeError(result.detail or "native music state command failed")
        payload = result.payload or {}
        payload.setdefault("ok", True)
        return payload

    def _run(self, args: list[str]) -> CommandResult:
        command = [sys.executable, str(DEFAULT_NATIVE_MUSIC_PLAYER), *args]
        completed = subprocess.run(
            command,
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
