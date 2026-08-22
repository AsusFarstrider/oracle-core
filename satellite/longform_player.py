from __future__ import annotations

import argparse
import ctypes
import json
import ntpath
import os
import shlex
import signal
import subprocess
import sys
import time
from shutil import which
from pathlib import Path
from typing import Any, Optional


STATE_DIR = Path("/tmp/oracle-longform-player")
STATE_PATH = STATE_DIR / "state.json"
PLAYLIST_PATH = STATE_DIR / "playlist.ffconcat"
LOG_PATH = STATE_DIR / "player.log"
_SUPPORTED_PLAYER_BASENAMES = {"ffplay", "mpv"}
_ORACLE_LONGFORM_PLAYLIST_MARKER = "oracle-longform-player/playlist.ffconcat"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Oracle long-form audio helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    play = subparsers.add_parser("play")
    play.add_argument("--manifest", required=True)
    play.add_argument("--player-bin", default="auto")

    pause = subparsers.add_parser("pause")
    pause.add_argument("--player-bin", default="auto")

    resume = subparsers.add_parser("resume")
    resume.add_argument("--player-bin", default="auto")

    stop = subparsers.add_parser("stop")
    stop.add_argument("--player-bin", default="auto")

    seek = subparsers.add_parser("seek")
    seek.add_argument("--position-seconds", required=True, type=float)
    seek.add_argument("--player-bin", default="auto")

    state = subparsers.add_parser("state")
    state.add_argument("--player-bin", default="auto")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "play":
        return _cmd_play(Path(args.manifest), player_bin=args.player_bin)
    if args.command == "pause":
        return _cmd_pause()
    if args.command == "resume":
        return _cmd_resume(player_bin=args.player_bin)
    if args.command == "stop":
        return _cmd_stop()
    if args.command == "seek":
        return _cmd_seek(args.position_seconds, player_bin=args.player_bin)
    if args.command == "state":
        return _cmd_state()
    return 1


def _cmd_play(manifest_path: Path, *, player_bin: str) -> int:
    manifest = _load_json(manifest_path)
    _ensure_state_dir()
    _stop_existing_process(remove_state=False)
    position_seconds = _clamp_position(float(manifest.get("start_position_seconds") or 0), manifest)
    state = _build_state(manifest, player_bin=player_bin, position_seconds=position_seconds)
    if bool(manifest.get("start_paused")):
        state["state"] = "paused"
    else:
        _start_player(state)
    _save_state(state)
    _print_json(_public_state(state))
    return 0


def _cmd_pause() -> int:
    state = _load_state()
    if state is None:
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    if state.get("state") != "playing":
        _print_json(_public_state(state))
        return 0
    state["position_seconds"] = _current_position(state)
    remaining = _stop_existing_process(remove_state=False)
    if remaining:
        state["state"] = "playing"
        state["pid"] = remaining[0]
        state["started_monotonic"] = None
        _save_state(state)
        print("Long-form pause did not terminate all Oracle-managed playback processes", file=sys.stderr)
        return 1
    state["state"] = "paused"
    state["pid"] = None
    state["started_monotonic"] = None
    _save_state(state)
    _print_json(_public_state(state))
    return 0


def _cmd_resume(*, player_bin: str) -> int:
    state = _load_state()
    if state is None:
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    if state.get("state") == "playing":
        _print_json(_public_state(state))
        return 0
    if state.get("state") not in {"paused", "stopped"}:
        _print_json(_public_state(state))
        return 0
    pid = int(state.get("pid") or 0)
    if pid > 0 and _is_process_alive(pid):
        os.kill(pid, signal.SIGCONT)
        state["state"] = "playing"
        state["started_monotonic"] = time.monotonic()
        _save_state(state)
        _print_json(_public_state(state))
        return 0

    state = _restart_from_position(state, _current_position(state), player_bin=player_bin)
    _print_json(_public_state(state))
    return 0


def _cmd_stop() -> int:
    state = _load_state()
    if state is None:
        remaining = _stop_existing_process(remove_state=False)
        if remaining:
            print("Long-form stop did not terminate all Oracle-managed playback processes", file=sys.stderr)
            return 1
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    position_seconds = _current_position(state)
    remaining = _stop_existing_process(remove_state=False)
    if remaining:
        state["position_seconds"] = position_seconds
        state["state"] = "playing"
        state["pid"] = remaining[0]
        state["started_monotonic"] = None
        _save_state(state)
        print("Long-form stop did not terminate all Oracle-managed playback processes", file=sys.stderr)
        return 1
    state["position_seconds"] = position_seconds
    state["state"] = "stopped"
    state["pid"] = None
    state["started_monotonic"] = None
    _save_state(state)
    _print_json(_public_state(state))
    return 0


def _cmd_seek(position_seconds: float, *, player_bin: str) -> int:
    state = _load_state()
    if state is None:
        raise SystemExit("No long-form playback state exists")
    _refresh_state(state)
    state = _restart_from_position(state, position_seconds, player_bin=player_bin)
    _print_json(_public_state(state))
    return 0


def _cmd_state() -> int:
    state = _load_state()
    if state is None:
        oracle_pids = _enumerate_oracle_longform_player_pids()
        if oracle_pids:
            _print_json(
                {
                    "ok": True,
                    "state": "playing",
                    "playing": True,
                    "degraded_state": True,
                    "degraded_reason": "orphan_longform_process",
                    "orphan_pids": oracle_pids,
                }
            )
            return 0
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    _save_state(state)
    payload = _public_state(state)
    tracked_pid = int(state.get("pid") or 0)
    oracle_pids = []
    if not payload.get("playing") and tracked_pid > 0:
        oracle_pids = _enumerate_oracle_longform_player_pids()
    if oracle_pids and (not payload.get("playing") or (tracked_pid > 0 and tracked_pid not in oracle_pids)):
        payload["state"] = "playing"
        payload["playing"] = True
        payload["degraded_state"] = True
        payload["degraded_reason"] = "orphan_longform_process"
        payload["orphan_pids"] = oracle_pids
    _print_json(payload)
    return 0


def _restart_from_position(state: dict[str, Any], position_seconds: float, *, player_bin: str) -> dict[str, Any]:
    manifest = dict(state.get("manifest") or {})
    if not manifest:
        raise SystemExit("Long-form state is missing the manifest")
    _stop_existing_process(remove_state=False)
    new_state = _build_state(
        manifest,
        player_bin=player_bin,
        position_seconds=_clamp_position(position_seconds, manifest),
    )
    _start_player(new_state)
    _save_state(new_state)
    return new_state


def _build_state(manifest: dict[str, Any], *, player_bin: str, position_seconds: float) -> dict[str, Any]:
    requested_player_bin = str(player_bin or "").strip() or "auto"
    return {
        "playback_id": str(manifest.get("playback_id", "")).strip(),
        "session_id": str(manifest.get("session_id", "")).strip(),
        "title": str(manifest.get("title", "")).strip(),
        "author": str(manifest.get("author", "")).strip(),
        "duration_seconds": float(manifest.get("duration_seconds") or 0),
        "position_seconds": position_seconds,
        "state": "starting",
        "pid": None,
        "started_monotonic": None,
        "manifest": manifest,
        "player_bin": requested_player_bin,
    }


def _start_player(state: dict[str, Any]) -> None:
    requested_player_bin = str(state.get("player_bin") or "").strip() or "auto"
    resolved_player_bin = _resolve_player_bin(requested_player_bin)
    player_command = _build_player_command(resolved_player_bin)
    state["player_bin"] = resolved_player_bin
    manifest = dict(state.get("manifest") or {})
    position_seconds = float(state.get("position_seconds") or 0)
    playlist_text = _build_ffconcat(manifest, position_seconds)
    PLAYLIST_PATH.write_text(playlist_text, encoding="utf-8")
    with LOG_PATH.open("ab") as log_handle:
        process = subprocess.Popen(
            player_command + [str(PLAYLIST_PATH)],
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    state["pid"] = process.pid
    state["state"] = "playing"
    state["started_monotonic"] = time.monotonic()


def _build_ffconcat(manifest: dict[str, Any], position_seconds: float) -> str:
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise SystemExit("Manifest does not include any tracks")

    selected = []
    current_track_found = False
    for track in tracks:
        if not isinstance(track, dict):
            continue
        start_offset = float(track.get("start_offset_seconds") or 0)
        duration = float(track.get("duration_seconds") or 0)
        if not current_track_found:
            if duration > 0 and position_seconds >= start_offset + duration:
                continue
            current_track_found = True
            selected.append((track, max(0.0, position_seconds - start_offset)))
        else:
            selected.append((track, 0.0))

    if not selected:
        selected.append((tracks[-1], 0.0))

    lines = ["ffconcat version 1.0"]
    for track, seek_offset in selected:
        url = str(track.get("url", "")).strip()
        if not url:
            continue
        lines.append(f"file {shlex.quote(url)}")
        if seek_offset > 0:
            lines.append(f"inpoint {seek_offset:.3f}")
    return "\n".join(lines) + "\n"


def _current_position(state: dict[str, Any]) -> float:
    base = float(state.get("position_seconds") or 0)
    if state.get("state") != "playing":
        return _cap_position(base, state)
    started_monotonic = state.get("started_monotonic")
    if started_monotonic is None:
        return _cap_position(base, state)
    return _cap_position(base + max(0.0, time.monotonic() - float(started_monotonic)), state)


def _cap_position(position_seconds: float, state: dict[str, Any]) -> float:
    duration = float(state.get("duration_seconds") or 0)
    if duration <= 0:
        return max(0.0, position_seconds)
    return max(0.0, min(position_seconds, duration))


def _clamp_position(position_seconds: float, manifest: dict[str, Any]) -> float:
    duration = float(manifest.get("duration_seconds") or 0)
    if duration <= 0:
        return max(0.0, position_seconds)
    return max(0.0, min(position_seconds, duration))


def _refresh_state(state: dict[str, Any]) -> None:
    pid = int(state.get("pid") or 0)
    if pid <= 0:
        return
    if _is_process_alive(pid):
        if state.get("state") == "starting":
            state["state"] = "playing"
        return
    final_position = _current_position(state)
    state["position_seconds"] = final_position
    state["state"] = "stopped"
    state["pid"] = None
    state["started_monotonic"] = None


def _stop_existing_process(*, remove_state: bool) -> list[int]:
    state = _load_state()
    tracked_pid = int((state or {}).get("pid") or 0)
    if tracked_pid > 0 and _is_process_alive(tracked_pid):
        _terminate_process(tracked_pid)
        remaining = _enumerate_oracle_longform_player_pids()
        if remaining:
            for pid in remaining:
                _terminate_process(pid)
            remaining = _enumerate_oracle_longform_player_pids()
        if remove_state:
            STATE_PATH.unlink(missing_ok=True)
        return remaining
    candidate_pids = _enumerate_oracle_longform_player_pids()
    if not candidate_pids:
        if remove_state:
            STATE_PATH.unlink(missing_ok=True)
        return []
    for pid in candidate_pids:
        _terminate_process(pid)
    if remove_state:
        STATE_PATH.unlink(missing_ok=True)
    return _enumerate_oracle_longform_player_pids()


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    position_seconds = _current_position(state)
    status = str(state.get("state") or "stopped")
    playing = status in {"playing", "paused"}
    return {
        "ok": True,
        "state": status,
        "playing": playing,
        "playback_id": str(state.get("playback_id", "")).strip(),
        "session_id": str(state.get("session_id", "")).strip(),
        "title": str(state.get("title", "")).strip(),
        "author": str(state.get("author", "")).strip(),
        "position_seconds": round(position_seconds, 3),
        "duration_seconds": float(state.get("duration_seconds") or 0),
    }


def _load_state() -> Optional[dict[str, Any]]:
    if not STATE_PATH.exists():
        return None
    return _load_json(STATE_PATH)


def _save_state(state: dict[str, Any]) -> None:
    _ensure_state_dir()
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return payload


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_windows_process_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _terminate_process(pid: int) -> None:
    if pid <= 0 or not _is_process_alive(pid):
        return
    if os.name == "nt":
        _terminate_windows_process_tree(pid)
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not _is_process_alive(pid):
            return
        time.sleep(0.05)
    if _is_process_alive(pid):
        os.kill(pid, signal.SIGKILL)


def _terminate_windows_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    for _ in range(20):
        if not _is_process_alive(pid):
            return
        time.sleep(0.05)


def _enumerate_oracle_longform_player_pids() -> list[int]:
    pids: list[int] = []
    for pid, argv in _iter_process_argv():
        if _is_oracle_longform_process_argv(argv):
            pids.append(pid)
    return sorted(set(pids))


def _iter_process_argv() -> list[tuple[int, list[str]]]:
    if os.name == "nt":
        return _iter_windows_process_argv()

    process_entries: list[tuple[int, list[str]]] = []
    proc_root = Path("/proc")
    if not proc_root.exists():
        return process_entries
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            payload = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not payload:
            continue
        argv = [part.decode("utf-8", errors="ignore") for part in payload.split(b"\x00") if part]
        if not argv:
            continue
        process_entries.append((int(entry.name), argv))
    return process_entries


def _iter_windows_process_argv() -> list[tuple[int, list[str]]]:
    if not _windows_supported_player_processes_present():
        return []
    script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'ffplay.exe' OR Name = 'mpv.exe'\" | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    process_entries: list[tuple[int, list[str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        command_line = str(row.get("CommandLine") or "").strip()
        if pid <= 0 or not command_line:
            continue
        try:
            argv = shlex.split(command_line, posix=False)
        except ValueError:
            argv = [command_line]
        process_entries.append((pid, argv or [command_line]))
    return process_entries


def _windows_supported_player_processes_present() -> bool:
    completed = subprocess.run(
        ["tasklist", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    process_list = completed.stdout.lower()
    return any(image_name in process_list for image_name in ("ffplay.exe", "mpv.exe"))


def _is_oracle_longform_process_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    if _player_basename(argv[0]) not in _SUPPORTED_PLAYER_BASENAMES:
        return False
    return _argv_contains_oracle_playlist(argv)


def _player_basename(value: str) -> str:
    raw = str(value or "").strip().strip('"')
    name = Path(raw).name
    if name == raw:
        name = ntpath.basename(raw)
    name = name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _argv_contains_oracle_playlist(argv: list[str]) -> bool:
    command_line = " ".join(str(part or "").strip().strip('"') for part in argv)
    normalized_command = command_line.replace("\\", "/").lower()
    normalized_playlist = str(PLAYLIST_PATH).replace("\\", "/").lower().lstrip("/")
    return normalized_playlist in normalized_command or _ORACLE_LONGFORM_PLAYLIST_MARKER in normalized_command


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload))


def _resolve_player_bin(player_bin: str) -> str:
    requested = str(player_bin or "").strip()
    if requested and requested != "auto":
        if _player_basename(requested) not in _SUPPORTED_PLAYER_BASENAMES:
            raise SystemExit(f"Unsupported long-form player binary: {requested}")
        resolved = which(requested)
        if resolved:
            return resolved
        raise SystemExit(f"Configured long-form player was not found or is not executable: {requested}")

    for candidate in ("ffplay", "mpv"):
        resolved = which(candidate)
        if resolved:
            return resolved
    raise SystemExit("No supported long-form player found. Install ffplay or mpv, or pass --player-bin explicitly.")


def _build_player_command(player_bin: str) -> list[str]:
    resolved = str(player_bin or "").strip()
    name = _player_basename(resolved)
    if name == "ffplay":
        return [
            resolved,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            "-safe",
            "0",
            "-protocol_whitelist",
            "file,http,https,tcp,tls",
        ]
    if name == "mpv":
        return [
            resolved,
            "--no-video",
            "--really-quiet",
            "--playlist",
        ]
    raise SystemExit(f"Unsupported long-form player binary: {resolved}")


if __name__ == "__main__":
    raise SystemExit(main())
