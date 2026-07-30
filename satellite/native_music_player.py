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
from pathlib import Path
from shutil import which
from typing import Any, Optional


STATE_DIR = Path("/tmp/oracle-native-music-player")
STATE_PATH = STATE_DIR / "state.json"
LOG_PATH = STATE_DIR / "player.log"
_SUPPORTED_PLAYER_BASENAMES = {"ffplay", "mpv"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Oracle native music player")
    subparsers = parser.add_subparsers(dest="command", required=True)

    play = subparsers.add_parser("play")
    play.add_argument("--url", required=True)
    play.add_argument("--track-id", default="")
    play.add_argument("--media-type", default="track")
    play.add_argument("--title", default="")
    play.add_argument("--artist", default="")
    play.add_argument("--album", default="")
    play.add_argument("--queue-id", default="")
    play.add_argument("--queue-position", type=int, default=0)
    play.add_argument("--queue-count", type=int, default=0)
    play.add_argument("--collection-title", default="")
    play.add_argument("--collection-type", default="")
    play.add_argument("--queue-tracks-json", default="")
    play.add_argument("--duration-seconds", type=float, default=0.0)
    play.add_argument("--player-bin", default="auto")

    pause = subparsers.add_parser("pause")
    resume = subparsers.add_parser("resume")
    resume.add_argument("--player-bin", default="auto")
    restart = subparsers.add_parser("restart")
    restart.add_argument("--player-bin", default="auto")
    stop = subparsers.add_parser("stop")
    state = subparsers.add_parser("state")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "play":
        return _cmd_play(
            url=str(args.url),
            track_id=str(args.track_id),
            media_type=str(args.media_type),
            title=str(args.title),
            artist=str(args.artist),
            album=str(args.album),
            queue_id=str(args.queue_id),
            queue_position=int(args.queue_position or 0),
            queue_count=int(args.queue_count or 0),
            collection_title=str(args.collection_title),
            collection_type=str(args.collection_type),
            queue_tracks=_parse_queue_tracks(str(args.queue_tracks_json)),
            duration_seconds=float(args.duration_seconds or 0.0),
            player_bin=str(args.player_bin),
        )
    if args.command == "pause":
        return _cmd_pause()
    if args.command == "resume":
        return _cmd_resume(player_bin=str(args.player_bin))
    if args.command == "restart":
        return _cmd_restart(player_bin=str(args.player_bin))
    if args.command == "stop":
        return _cmd_stop()
    if args.command == "state":
        return _cmd_state()
    return 1


def _cmd_play(
    *,
    url: str,
    track_id: str,
    media_type: str,
    title: str,
    artist: str,
    album: str,
    queue_id: str,
    queue_position: int,
    queue_count: int,
    collection_title: str,
    collection_type: str,
    queue_tracks: list[dict[str, Any]] | None,
    duration_seconds: float,
    player_bin: str,
) -> int:
    if not url.strip():
        raise SystemExit("Track URL is required")
    _ensure_state_dir()
    _stop_existing_process(remove_state=False)
    state = _build_state(
        url=url.strip(),
        track_id=track_id.strip(),
        media_type=media_type.strip(),
        title=title.strip(),
        artist=artist.strip(),
        album=album.strip(),
        queue_id=queue_id.strip(),
        queue_position=max(0, queue_position),
        queue_count=max(0, queue_count),
        collection_title=collection_title.strip(),
        collection_type=collection_type.strip(),
        queue_tracks=_normalize_queue_tracks(queue_tracks),
        duration_seconds=max(0.0, duration_seconds),
        position_seconds=0.0,
        player_bin=player_bin,
    )
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
        state["pgid"] = remaining[0]
        state["started_monotonic"] = None
        _save_state(state)
        print("Native music pause did not terminate all Oracle-managed playback processes", file=sys.stderr)
        return 1
    state["state"] = "paused"
    state["pid"] = None
    state["pgid"] = None
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
    if state.get("state") != "paused":
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
    restarted = _restart_from_position(state, _current_position(state), player_bin=player_bin)
    _print_json(_public_state(restarted))
    return 0


def _cmd_stop() -> int:
    state = _load_state()
    if state is None:
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    state["position_seconds"] = _current_position(state)
    remaining = _stop_existing_process(remove_state=False)
    if remaining:
        state["state"] = "playing"
        state["pid"] = remaining[0]
        state["pgid"] = remaining[0]
        state["started_monotonic"] = None
        _save_state(state)
        print("Native music stop did not terminate all Oracle-managed playback processes", file=sys.stderr)
        return 1
    state["state"] = "stopped"
    state["pid"] = None
    state["pgid"] = None
    state["started_monotonic"] = None
    _save_state(state)
    _print_json(_public_state(state))
    return 0


def _cmd_restart(*, player_bin: str) -> int:
    state = _load_state()
    if state is None:
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    if str(state.get("state") or "").strip().lower() == "stopped":
        _print_json(_public_state(state))
        return 0
    restarted = _restart_from_position(state, 0.0, player_bin=player_bin)
    _print_json(_public_state(restarted))
    return 0


def _cmd_state() -> int:
    state = _load_state()
    if state is None:
        _print_json({"ok": True, "state": "stopped", "playing": False})
        return 0
    _refresh_state(state)
    _save_state(state)
    payload = _public_state(state)
    tracked_pid = int(state.get("pid") or 0)
    orphan_pids = []
    if not payload.get("playing") and tracked_pid > 0:
        orphan_pids = _enumerate_oracle_native_music_player_pids(state)
    if orphan_pids and (not payload.get("playing") or (tracked_pid > 0 and tracked_pid not in orphan_pids)):
        payload["state"] = "playing"
        payload["playing"] = True
        payload["degraded_state"] = True
        payload["degraded_reason"] = "orphan_native_music_process"
        payload["orphan_pids"] = orphan_pids
    _print_json(payload)
    return 0


def _restart_from_position(state: dict[str, Any], position_seconds: float, *, player_bin: str) -> dict[str, Any]:
    _stop_existing_process(remove_state=False)
    new_state = _build_state(
        url=str(state.get("url", "")).strip(),
        track_id=str(state.get("track_id", "")).strip(),
        media_type=str(state.get("media_type", "")).strip(),
        title=str(state.get("title", "")).strip(),
        artist=str(state.get("artist", "")).strip(),
        album=str(state.get("album", "")).strip(),
        queue_id=str(state.get("queue_id", "")).strip(),
        queue_position=int(state.get("queue_position") or 0),
        queue_count=int(state.get("queue_count") or 0),
        collection_title=str(state.get("collection_title", "")).strip(),
        collection_type=str(state.get("collection_type", "")).strip(),
        queue_tracks=_normalize_queue_tracks(state.get("queue_tracks")),
        duration_seconds=float(state.get("duration_seconds") or 0.0),
        position_seconds=_cap_position(position_seconds, state),
        player_bin=player_bin,
    )
    _start_player(new_state)
    _save_state(new_state)
    return new_state


def _build_state(
    *,
    url: str,
    track_id: str,
    media_type: str,
    title: str,
    artist: str,
    album: str,
    queue_id: str,
    queue_position: int,
    queue_count: int,
    collection_title: str,
    collection_type: str,
    queue_tracks: list[dict[str, Any]] | None,
    duration_seconds: float,
    position_seconds: float,
    player_bin: str,
) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "media_type": media_type or "track",
        "url": url,
        "title": title,
        "artist": artist,
        "album": album,
        "queue_id": queue_id,
        "queue_position": queue_position,
        "queue_count": queue_count,
        "collection_title": collection_title,
        "collection_type": collection_type,
        "queue_tracks": _normalize_queue_tracks(queue_tracks),
        "duration_seconds": duration_seconds,
        "position_seconds": position_seconds,
        "state": "starting",
        "pid": None,
        "pgid": None,
        "started_monotonic": None,
        "player_bin": _resolve_player_bin(player_bin),
    }


def _start_player(state: dict[str, Any]) -> None:
    player_command = _build_player_command(
        player_bin=str(state.get("player_bin") or ""),
        url=str(state.get("url") or ""),
        position_seconds=float(state.get("position_seconds") or 0.0),
    )
    with LOG_PATH.open("ab") as log_handle:
        process = subprocess.Popen(
            player_command,
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    state["pid"] = process.pid
    state["pgid"] = process.pid
    state["state"] = "playing"
    state["started_monotonic"] = time.monotonic()


def _current_position(state: dict[str, Any]) -> float:
    base = float(state.get("position_seconds") or 0.0)
    if state.get("state") != "playing":
        return _cap_position(base, state)
    started_monotonic = state.get("started_monotonic")
    if started_monotonic is None:
        return _cap_position(base, state)
    return _cap_position(base + max(0.0, time.monotonic() - float(started_monotonic)), state)


def _cap_position(position_seconds: float, state: dict[str, Any]) -> float:
    duration = float(state.get("duration_seconds") or 0.0)
    if duration <= 0:
        return max(0.0, position_seconds)
    return max(0.0, min(position_seconds, duration))


def _refresh_state(state: dict[str, Any]) -> None:
    pid = int(state.get("pid") or 0)
    if pid <= 0:
        if str(state.get("state") or "").strip().lower() in {"paused", "stopped"}:
            return
        orphan_pids = _enumerate_oracle_native_music_player_pids(state)
        if orphan_pids:
            state["pid"] = orphan_pids[0]
            state["pgid"] = orphan_pids[0]
            state["state"] = "playing"
            state["started_monotonic"] = None
        return
    if _is_process_alive(pid):
        if state.get("state") == "starting":
            state["state"] = "playing"
        return
    orphan_pids = _enumerate_oracle_native_music_player_pids(state)
    if orphan_pids:
        state["pid"] = orphan_pids[0]
        state["pgid"] = orphan_pids[0]
        state["state"] = "playing"
        state["started_monotonic"] = None
        return
    state["position_seconds"] = _current_position(state)
    state["state"] = "stopped"
    state["pid"] = None
    state["pgid"] = None
    state["started_monotonic"] = None


def _stop_existing_process(*, remove_state: bool) -> list[int]:
    state = _load_state()
    if state is None:
        if remove_state and STATE_PATH.exists():
            STATE_PATH.unlink(missing_ok=True)
        return []
    target_pids: list[int] = []
    pid = int(state.get("pid") or 0)
    pgid = int(state.get("pgid") or 0)
    if pid > 0 and _is_process_alive(pid):
        tracked_target = pgid or pid
        if _is_process_alive(tracked_target):
            _terminate_process(tracked_target)
        remaining = _enumerate_oracle_native_music_player_pids(state)
        if remaining:
            for candidate in remaining:
                if _is_process_alive(candidate):
                    _terminate_process(candidate)
            remaining = _enumerate_oracle_native_music_player_pids(state)
        if remove_state:
            STATE_PATH.unlink(missing_ok=True)
        return remaining
    target_pids = _enumerate_oracle_native_music_player_pids(state)
    if not target_pids:
        if remove_state:
            STATE_PATH.unlink(missing_ok=True)
        return []
    for candidate in target_pids:
        if _is_process_alive(candidate):
            _terminate_process(candidate)
    remaining = _enumerate_oracle_native_music_player_pids(state)
    if remove_state:
        STATE_PATH.unlink(missing_ok=True)
    return remaining


def _terminate_process(pid: int) -> None:
    _signal_process_tree(pid, signal.SIGTERM)
    for _ in range(20):
        if not _is_process_alive(pid):
            return
        time.sleep(0.05)
    if _is_process_alive(pid):
        _signal_process_tree(pid, signal.SIGKILL)


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    position_seconds = _current_position(state)
    current_state = str(state.get("state") or "stopped")
    return {
        "ok": True,
        "state": current_state,
        "playing": current_state in {"playing", "paused"},
        "backend_type": "oracle_native_music",
        "track_id": str(state.get("track_id", "")).strip(),
        "media_type": str(state.get("media_type", "")).strip() or "track",
        "title": str(state.get("title", "")).strip(),
        "artist": str(state.get("artist", "")).strip(),
        "album": str(state.get("album", "")).strip(),
        "queue_id": str(state.get("queue_id", "")).strip(),
        "queue_position": int(state.get("queue_position") or 0),
        "queue_count": int(state.get("queue_count") or 0),
        "collection_title": str(state.get("collection_title", "")).strip(),
        "collection_type": str(state.get("collection_type", "")).strip(),
        "queue_tracks": _normalize_queue_tracks(state.get("queue_tracks")),
        "has_previous": _has_previous(state),
        "has_next": _has_next(state),
        "position_seconds": round(position_seconds, 3),
        "duration_seconds": float(state.get("duration_seconds") or 0.0),
    }


def _parse_queue_tracks(raw: str) -> list[dict[str, Any]] | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit("queue-tracks-json must be valid JSON") from exc
    return _normalize_queue_tracks(parsed)


def _normalize_queue_tracks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        track_id = str(item.get("rating_key") or item.get("plex_key") or "").strip()
        plex_key = str(item.get("plex_key") or "").strip()
        title = str(item.get("title") or "").strip()
        if not track_id or not title:
            continue
        normalized.append(
            {
                "rating_key": track_id,
                "plex_key": plex_key,
                "parent_key": str(item.get("parent_key") or "").strip(),
                "title": title,
                "artist": str(item.get("artist") or "").strip(),
                "album": str(item.get("album") or "").strip(),
                "duration_seconds": float(item.get("duration_seconds") or 0.0),
            }
        )
    return normalized


def _has_previous(state: dict[str, Any]) -> bool:
    queue_tracks = _normalize_queue_tracks(state.get("queue_tracks"))
    queue_position = int(state.get("queue_position") or 0)
    if queue_tracks:
        return len(queue_tracks) > 1 and queue_position > 1
    return queue_position > 1


def _has_next(state: dict[str, Any]) -> bool:
    queue_tracks = _normalize_queue_tracks(state.get("queue_tracks"))
    queue_position = int(state.get("queue_position") or 0)
    queue_count = max(len(queue_tracks), int(state.get("queue_count") or 0))
    return queue_count > 0 and queue_position < queue_count


def _load_state() -> Optional[dict[str, Any]]:
    if not STATE_PATH.exists():
        return None
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected a JSON object in {STATE_PATH}")
    return payload


def _save_state(state: dict[str, Any]) -> None:
    _ensure_state_dir()
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


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


def _signal_process_tree(pid: int, sig: int) -> None:
    if os.name == "nt":
        _terminate_windows_process_tree(pid)
        return
    try:
        os.killpg(pid, sig)
        return
    except OSError:
        pass
    try:
        os.kill(pid, sig)
    except OSError:
        return


def _terminate_windows_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )


def _iter_process_argv() -> list[tuple[int, list[str]]]:
    if os.name == "nt":
        return _iter_windows_process_argv()

    processes: list[tuple[int, list[str]]] = []
    proc_root = Path("/proc")
    if not proc_root.exists():
        return processes
    for entry in proc_root.iterdir():
        name = entry.name
        if not name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        argv = [part.decode("utf-8", errors="ignore") for part in raw.split(b"\0") if part]
        if not argv:
            continue
        processes.append((int(name), argv))
    return processes


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
    processes: list[tuple[int, list[str]]] = []
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
        processes.append((pid, argv or [command_line]))
    return processes


def _windows_supported_player_processes_present() -> bool:
    completed = subprocess.run(
        ["tasklist", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    process_list = completed.stdout.lower()
    return any(image_name in process_list for image_name in ("ffplay.exe", "mpv.exe"))


def _enumerate_oracle_native_music_player_pids(state: dict[str, Any] | None = None) -> list[int]:
    if not isinstance(state, dict):
        return []
    url = str(state.get("url") or "").strip()
    if not url:
        return []
    player_bin = _player_basename(str(state.get("player_bin") or ""))
    allowed_names = {player_bin} if player_bin in _SUPPORTED_PLAYER_BASENAMES else set(_SUPPORTED_PLAYER_BASENAMES)
    matching_pids: list[int] = []
    for pid, argv in _iter_process_argv():
        executable_name = _player_basename(argv[0])
        if executable_name not in allowed_names:
            continue
        if url not in " ".join(str(part or "").strip().strip('"') for part in argv):
            continue
        matching_pids.append(pid)
    return matching_pids


def _player_basename(value: str) -> str:
    raw = str(value or "").strip().strip('"')
    name = Path(raw).name
    if name == raw:
        name = ntpath.basename(raw)
    name = name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _resolve_player_bin(player_bin: str) -> str:
    requested = str(player_bin or "").strip()
    if requested and requested != "auto":
        return requested
    for candidate in ("ffplay", "mpv"):
        resolved = which(candidate)
        if resolved:
            return resolved
    raise SystemExit("No supported native music player found. Install ffplay or mpv, or pass --player-bin explicitly.")


def _build_player_command(*, player_bin: str, url: str, position_seconds: float) -> list[str]:
    resolved = str(player_bin or "").strip()
    name = _player_basename(resolved)
    if name == "ffplay":
        command = [
            resolved,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
        if position_seconds > 0:
            command.extend(["-ss", f"{position_seconds:.3f}"])
        command.append(url)
        return command
    if name == "mpv":
        command = [
            resolved,
            "--no-video",
            "--really-quiet",
        ]
        if position_seconds > 0:
            command.append(f"--start={position_seconds:.3f}")
        command.append(url)
        return command
    raise SystemExit(f"Unsupported native music player binary: {resolved}")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload))


if __name__ == "__main__":
    raise SystemExit(main())
