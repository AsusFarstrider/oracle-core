from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request
from xml.etree import ElementTree

from satellite.control_service_runtime.longform import CommandResult, LongformShellController
from satellite.control_service_runtime.native_music import NativeMusicController
from satellite.control_service_runtime.system_volume import SystemVolumeController, build_system_volume_config

from .common import safe_int


@dataclass
class PausedPlaybackSnapshot:
    media_type: str
    plex_key: str
    title: str
    artist: str
    album: str
    stored_at: float


class LocalPlaybackAdapter:
    def __init__(self, args: argparse.Namespace) -> None:
        self._longform = LongformShellController(args)
        self._supports_oracle_native_music = bool(getattr(args, "supports_oracle_native_music", False))
        self._native_music = NativeMusicController(args) if self._supports_oracle_native_music else None
        self.base_url = str(args.plexamp_url).rstrip("/")
        self.plex_server_url = str(args.plex_server_url).rstrip("/")
        self.plex_token = str(args.plex_token).strip()
        self.plex_machine_identifier = str(args.plex_machine_identifier).strip()
        self._disable_plexamp_external = bool(getattr(args, "disable_plexamp_external", False))
        self.http_timeout_seconds = float(args.http_timeout_seconds)
        self._paused_snapshot: PausedPlaybackSnapshot | None = None
        self._paused_snapshot_max_age_seconds = 15 * 60.0
        self._system_volume = SystemVolumeController(build_system_volume_config(args))

    def health(self) -> dict[str, Any]:
        try:
            state = self.get_now_playing()
            return {
                "adapter": "local_playback",
                "plexamp_url": self.base_url,
                "music_backend_expectation": self.get_music_backend_expectation(),
                "oracle_native_music": self._native_music.health() if self._native_music is not None else {"enabled": False},
                "playback_state": state.get("state", "unknown"),
                "system_volume": self._system_volume.health_payload(),
                "longform": self._longform.health(),
            }
        except Exception as exc:
            return {
                "adapter": "local_playback",
                "plexamp_url": self.base_url,
                "music_backend_expectation": self.get_music_backend_expectation(),
                "oracle_native_music": self._native_music.health() if self._native_music is not None else {"enabled": False},
                "error": str(exc),
                "system_volume": self._system_volume.health_payload(),
                "longform": self._longform.health(),
            }

    def get_music_backend_expectation(self) -> dict[str, Any]:
        native_enabled = bool(self._native_music is not None and self._native_music.is_enabled())
        plexamp_enabled = self._supports_plexamp_external()
        return {
            "default_backend": "oracle_native_music" if native_enabled else "plexamp_external",
            "oracle_native_music_enabled": native_enabled,
            "supports_oracle_native_music": bool(self._supports_oracle_native_music),
            "supports_plexamp": plexamp_enabled,
        }

    def pause(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return self._native_music.pause()
        baseline_state = self.get_now_playing()
        result = self._simple_action("pause")
        if result.ok and self._resume_snapshot_from_state(baseline_state) is not None:
            self._paused_snapshot = self._resume_snapshot_from_state(baseline_state)
        return result

    def resume(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_resumable(native_state):
            return self._native_music.resume()
        baseline_state = self.get_now_playing()
        current_state = str(baseline_state.get("state", "")).strip().lower()
        if current_state in {"paused", "buffering"} and bool(baseline_state.get("playing")):
            result = self._simple_action("play")
            if not result.ok:
                return result
            timeline, confirmed = self._poll_until_playing(
                command_id=int(time.time() * 1000) % 1000000,
                allow_paused=False,
            )
            if confirmed:
                self._paused_snapshot = None
                state = self._extract_timeline_state(timeline)
                return CommandResult(
                    ok=True,
                    state="accepted",
                    payload={
                        "title": state.get("title", ""),
                        "artist": state.get("artist", ""),
                        "album": state.get("album", ""),
                        "playback_state": state.get("state", "unknown"),
                    },
                )
            return CommandResult(
                ok=False,
                state="failed",
                detail="Plexamp accepted resume but did not enter a playable state",
            )
        snapshot = self._fresh_paused_snapshot()
        if snapshot is None:
            return CommandResult(ok=False, state="failed", detail="No paused Plex playback is available to resume")
        result = self.play_media(
            media_type=snapshot.media_type,
            plex_key=snapshot.plex_key,
            title=snapshot.title,
            artist=snapshot.artist,
            album=snapshot.album,
        )
        if result.ok:
            self._paused_snapshot = None
        return result

    def stop(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return self._native_music.stop()
        baseline_state = self.get_now_playing()
        result = self._request("/player/playback/stop", {})
        if not result.ok:
            return result
        if not baseline_state.get("playing"):
            return CommandResult(ok=True, state="stopped", payload={"playback_state": "stopped"})

        timeline, confirmed = self._poll_until_stopped(command_id=int(time.time() * 1000) % 1000000)
        state = self._extract_timeline_state(timeline)
        payload = {
            "title": state.get("title", ""),
            "artist": state.get("artist", ""),
            "album": state.get("album", ""),
            "playback_state": state.get("state", "unknown"),
        }
        if not confirmed:
            return CommandResult(
                ok=False,
                state="failed",
                detail="Plexamp accepted stop but remained in a playable state",
                payload=payload,
            )
        return CommandResult(ok=True, state="stopped", payload=payload)

    def next(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return self._advance_native_queue(native_state, delta=1)
        self._paused_snapshot = None
        return self._simple_action("skipNext")

    def previous(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return self._advance_native_queue(native_state, delta=-1)
        self._paused_snapshot = None
        return self._simple_action("skipPrevious")

    def restart(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return self._native_music.restart()
        return CommandResult(ok=False, state="unsupported", detail="restart is only supported for oracle_native_music")

    def set_volume(self, level: int) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state) and self._system_volume.is_enabled():
            return self._system_volume.set_volume(level)
        if self._system_volume.is_enabled():
            return self._system_volume.set_volume(level)
        result = self._request("/player/playback/setParameters", {"volume": level})
        if not result.ok:
            return result
        confirmed_level = self._poll_until_volume(level, command_id=int(time.time() * 1000) % 1000000)
        if confirmed_level == level:
            return CommandResult(ok=True, state="accepted", payload={"volume_level": level})
        return CommandResult(
            ok=False,
            state="failed",
            detail="Plexamp accepted set_volume but did not report the requested level",
            payload={"volume_level": confirmed_level},
        )

    def volume_up(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state) and self._system_volume.is_enabled():
            return self._system_volume.volume_up()
        if self._system_volume.is_enabled():
            return self._system_volume.volume_up()
        return self._adjust_volume(10)

    def volume_down(self) -> CommandResult:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state) and self._system_volume.is_enabled():
            return self._system_volume.volume_down()
        if self._system_volume.is_enabled():
            return self._system_volume.volume_down()
        return self._adjust_volume(-10)

    def get_output_volume(self) -> int | None:
        if self._system_volume.is_enabled():
            return self._system_volume.current_level()
        now_playing = self.get_now_playing()
        return safe_int(now_playing.get("volume"))

    def play_media(
        self,
        media_type: str,
        plex_key: str,
        title: str = "",
        artist: str = "",
        album: str = "",
        parent_key: str = "",
        rating_key: str = "",
        duration_seconds: float = 0.0,
        backend_hint: str = "",
        queue_id: str = "",
        queue_position: int = 0,
        queue_count: int = 0,
        collection_title: str = "",
        collection_type: str = "",
        queue_tracks: list[dict[str, Any]] | None = None,
    ) -> CommandResult:
        self._paused_snapshot = None
        selected_backend = self._select_music_backend(media_type=media_type, backend_hint=backend_hint)
        if selected_backend == "oracle_native_music":
            self._stop_plexamp_if_active()
            native_result = self._play_media_native(
                media_type=media_type,
                rating_key=rating_key,
                plex_key=plex_key,
                title=title,
                artist=artist,
                album=album,
                duration_seconds=duration_seconds,
                queue_id=queue_id,
                queue_position=queue_position,
                queue_count=queue_count,
                collection_title=collection_title,
                collection_type=collection_type,
                queue_tracks=queue_tracks,
            )
            if native_result.ok:
                return native_result
        self._stop_native_music_if_active()
        if not self.plex_server_url or not self.plex_token or not self.plex_machine_identifier:
            return CommandResult(
                ok=False,
                state="invalid_configuration",
                detail="plex_server_url, plex_token, and plex_machine_identifier are required for play_media",
            )

        baseline_state = self.get_now_playing()

        params = {
            "source": self.plex_machine_identifier,
            "machineIdentifier": self.plex_machine_identifier,
            "address": parse.urlsplit(self.plex_server_url).hostname or "",
            "port": parse.urlsplit(self.plex_server_url).port or 32400,
            "protocol": parse.urlsplit(self.plex_server_url).scheme or "http",
            "token": self.plex_token,
            "includeExternalMedia": 1,
            "commandID": int(time.time() * 1000) % 1000000,
        }
        create_params = dict(params)
        create_params["type"] = "audio"
        create_params["uri"] = self._build_playback_uri(plex_key)
        create_result = self._request("/player/playback/createPlayQueue", create_params)
        if not create_result.ok:
            return create_result

        timeline, confirmed = self._poll_until_playing(
            command_id=int(create_params["commandID"]) + 1,
            baseline_state=baseline_state,
            media_type=media_type,
            expected_title=title,
            expected_artist=artist,
            expected_album=album,
        )
        state = self._extract_timeline_state(timeline)
        payload = {
            "title": state.get("title", title),
            "artist": state.get("artist", artist),
            "album": state.get("album", album),
            "playback_state": state.get("state", "unknown"),
        }
        if state.get("state") not in {"playing", "paused", "buffering"} or not confirmed:
            return CommandResult(
                ok=False,
                state="failed",
                detail="Plexamp accepted play_media but did not enter a playable state",
                payload=payload,
            )
        return CommandResult(ok=True, state="accepted", payload=payload)

    def get_now_playing(self) -> dict[str, Any]:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            return {
                "ok": True,
                "playing": native_state.get("state") in {"playing", "paused", "starting"},
                "state": native_state.get("state"),
                "backend_type": "oracle_native_music",
                "type": native_state.get("media_type", "track"),
                "plex_key": str(native_state.get("track_id", "")).strip(),
                "title": native_state.get("title", ""),
                "artist": native_state.get("artist", ""),
                "album": native_state.get("album", ""),
                "queue_id": native_state.get("queue_id", ""),
                "queue_position": native_state.get("queue_position"),
                "queue_count": native_state.get("queue_count"),
                "collection_title": native_state.get("collection_title", ""),
                "collection_type": native_state.get("collection_type", ""),
                "position_seconds": native_state.get("position_seconds"),
                "duration_seconds": native_state.get("duration_seconds"),
                "volume": self._system_volume.current_level() if self._system_volume.is_enabled() else None,
            }
        if not self._supports_plexamp_external():
            return {"ok": True, "playing": False, "state": "stopped"}
        timeline = self._timeline(include_metadata=True, command_id=1)
        state = self._extract_timeline_state(timeline)
        if state.get("state") == "stopped":
            return {"ok": True, "playing": False}
        return {
            "ok": True,
            "playing": state.get("state") in {"playing", "paused", "buffering"},
            "state": state.get("state"),
            "backend_type": "plexamp_external",
            "type": state.get("type", ""),
            "plex_key": state.get("plex_key", ""),
            "title": state.get("title", ""),
            "artist": state.get("artist", ""),
            "album": state.get("album", ""),
            "volume": self._system_volume.current_level() if self._system_volume.is_enabled() else state.get("volume"),
        }

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

    def _adjust_volume(self, delta: int) -> CommandResult:
        timeline = self._timeline(include_metadata=True, command_id=1)
        state = self._extract_timeline_state(timeline)
        current = safe_int(state.get("volume"))
        if current is None:
            return CommandResult(ok=False, state="failed", detail="Current Plexamp volume is unavailable")
        level = max(0, min(100, current + delta))
        return self.set_volume(level)

    def _select_music_backend(self, *, media_type: str, backend_hint: str) -> str:
        normalized_hint = str(backend_hint or "").strip().lower()
        if normalized_hint == "oracle_native_music" and self._can_use_oracle_native_music(media_type):
            return "oracle_native_music"
        if normalized_hint == "plexamp_external":
            return "plexamp_external"
        if self._can_use_oracle_native_music(media_type):
            return "oracle_native_music"
        return "plexamp_external"

    def _can_use_oracle_native_music(self, media_type: str) -> bool:
        normalized_media_type = str(media_type or "").strip().lower()
        return (
            self._native_music is not None
            and self._native_music.is_enabled()
            and normalized_media_type in {"track", "album", "artist", "playlist"}
        )

    def _play_media_native(
        self,
        *,
        media_type: str,
        rating_key: str,
        plex_key: str,
        title: str,
        artist: str,
        album: str,
        duration_seconds: float,
        queue_id: str,
        queue_position: int,
        queue_count: int,
        collection_title: str,
        collection_type: str,
        queue_tracks: list[dict[str, Any]] | None,
    ) -> CommandResult:
        selected_track = self._select_native_queue_track(queue_tracks, queue_position)
        track_id = str(
            (selected_track or {}).get("rating_key")
            or (selected_track or {}).get("plex_key")
            or rating_key
            or plex_key
        ).strip()
        if not track_id:
            return CommandResult(ok=False, state="failed", detail="oracle_native_music requires a track rating key")
        title = str((selected_track or {}).get("title") or title).strip()
        artist = str((selected_track or {}).get("artist") or artist).strip()
        album = str((selected_track or {}).get("album") or album).strip()
        try:
            duration_seconds = float((selected_track or {}).get("duration_seconds") or duration_seconds or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        stream_url = self._build_native_stream_url(track_id)
        result = self._native_music.play_track(
            stream_url=stream_url,
            track_id=track_id,
            media_type=media_type,
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            queue_id=str(queue_id or track_id).strip(),
            queue_position=max(1, int(queue_position or 1)),
            queue_count=max(len(queue_tracks or []), int(queue_count or 0), 1),
            collection_title=str(collection_title or album or title).strip(),
            collection_type=str(collection_type or media_type or "track").strip(),
            queue_tracks=queue_tracks,
        )
        if not result.ok:
            return result
        payload = dict(result.payload or {})
        payload.setdefault("backend_type", "oracle_native_music")
        payload.setdefault("playback_state", payload.get("state", "unknown"))
        return CommandResult(ok=True, state="accepted", payload=payload)

    def _select_native_queue_track(
        self,
        queue_tracks: list[dict[str, Any]] | None,
        queue_position: int,
    ) -> dict[str, Any] | None:
        if not isinstance(queue_tracks, list) or not queue_tracks:
            return None
        normalized_tracks = [track for track in queue_tracks if isinstance(track, dict)]
        if not normalized_tracks:
            return None
        index = max(0, int(queue_position or 1) - 1)
        if index >= len(normalized_tracks):
            index = 0
        return normalized_tracks[index]

    def _build_native_stream_url(self, rating_key: str) -> str:
        if not self.plex_server_url or not self.plex_token:
            raise RuntimeError("plex_server_url and plex_token are required for oracle_native_music")
        metadata = self._fetch_plex_metadata(str(rating_key).strip())
        stream_key = self._extract_stream_part_key(metadata)
        separator = "&" if "?" in stream_key else "?"
        if stream_key.startswith("http://") or stream_key.startswith("https://"):
            return f"{stream_key}{separator}X-Plex-Token={parse.quote(self.plex_token)}"
        return f"{self.plex_server_url}{stream_key}{separator}X-Plex-Token={parse.quote(self.plex_token)}"

    def _advance_native_queue(self, native_state: dict[str, Any] | None, *, delta: int) -> CommandResult:
        if not isinstance(native_state, dict):
            return CommandResult(ok=False, state="failed", detail="oracle_native_music state is unavailable")
        queue_tracks = native_state.get("queue_tracks")
        normalized_tracks = [track for track in queue_tracks if isinstance(track, dict)] if isinstance(queue_tracks, list) else []
        if not normalized_tracks:
            return CommandResult(ok=False, state="unsupported", detail="oracle_native_music queue transport requires persisted queue tracks")
        current_position = max(1, int(native_state.get("queue_position") or 1))
        current_index = min(len(normalized_tracks) - 1, current_position - 1)
        target_index = current_index + delta
        if target_index < 0 or target_index >= len(normalized_tracks):
            return CommandResult(ok=False, state="unsupported", detail="native queue boundary reached")
        target_track = normalized_tracks[target_index]
        track_id = str(target_track.get("rating_key") or target_track.get("plex_key") or "").strip()
        if not track_id:
            return CommandResult(ok=False, state="failed", detail="native queue item is missing a track id")
        stream_url = self._build_native_stream_url(track_id)
        try:
            duration_seconds = float(target_track.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        result = self._native_music.play_track(
            stream_url=stream_url,
            track_id=track_id,
            media_type=str(native_state.get("media_type") or "track").strip(),
            title=str(target_track.get("title") or "").strip(),
            artist=str(target_track.get("artist") or "").strip(),
            album=str(target_track.get("album") or "").strip(),
            duration_seconds=duration_seconds,
            queue_id=str(native_state.get("queue_id") or track_id).strip(),
            queue_position=target_index + 1,
            queue_count=max(len(normalized_tracks), int(native_state.get("queue_count") or 0), 1),
            collection_title=str(native_state.get("collection_title") or target_track.get("album") or target_track.get("title") or "").strip(),
            collection_type=str(native_state.get("collection_type") or native_state.get("media_type") or "track").strip(),
            queue_tracks=normalized_tracks,
        )
        if not result.ok:
            return result
        payload = dict(result.payload or {})
        payload.setdefault("backend_type", "oracle_native_music")
        payload.setdefault("playback_state", payload.get("state", "unknown"))
        return CommandResult(ok=True, state="accepted", payload=payload)

    def _fetch_plex_metadata(self, rating_key: str) -> str:
        endpoint = f"{self.plex_server_url}/library/metadata/{parse.quote(rating_key)}?{parse.urlencode({'X-Plex-Token': self.plex_token})}"
        req = request.Request(endpoint, method="GET")
        try:
            with request.urlopen(req, timeout=self.http_timeout_seconds) as response:
                return response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(detail or f"Plex metadata request returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    def _extract_stream_part_key(self, metadata_payload: str) -> str:
        try:
            root = ElementTree.fromstring(metadata_payload)
        except ElementTree.ParseError as exc:
            raise RuntimeError("Plex metadata returned invalid XML") from exc
        for track in root.findall(".//Track"):
            for media in track.findall("Media"):
                for part in media.findall("Part"):
                    key = str(part.attrib.get("key", "")).strip()
                    if key:
                        return key
        raise RuntimeError("Plex metadata did not include a playable media part")

    def _safe_native_music_state(self) -> dict[str, Any] | None:
        if self._native_music is None:
            return None
        try:
            return self._native_music.state()
        except Exception:
            return None

    def _stop_native_music_if_active(self) -> None:
        native_state = self._safe_native_music_state()
        if self._native_music_active(native_state):
            self._native_music.stop()

    def _stop_plexamp_if_active(self) -> None:
        if not self._supports_plexamp_external():
            return
        try:
            self._request("/player/playback/stop", {})
        except Exception:
            return

    def _supports_plexamp_external(self) -> bool:
        return bool(
            not self._disable_plexamp_external
            and self.plex_server_url
            and self.plex_token
            and self.plex_machine_identifier
        )

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

    def _native_music_active(self, state: dict[str, Any] | None) -> bool:
        if not isinstance(state, dict):
            return False
        return str(state.get("state", "")).strip().lower() in {"playing", "paused", "starting"}

    def _native_music_resumable(self, state: dict[str, Any] | None) -> bool:
        if not isinstance(state, dict):
            return False
        return str(state.get("state", "")).strip().lower() in {"paused", "playing", "starting"}

    def _simple_action(self, action: str) -> CommandResult:
        return self._request(f"/player/playback/{action}", {})

    def _timeline(self, *, include_metadata: bool, command_id: int) -> str:
        params = {
            "wait": 0,
            "commandID": command_id,
            "type": "music",
        }
        if include_metadata:
            params["includeMetadata"] = 1
        response = self._request("/player/timeline/poll", params, expect_body=True)
        if not response.ok:
            raise RuntimeError(response.detail or "timeline poll failed")
        return str((response.payload or {}).get("raw_xml", ""))

    def _poll_until_playing(
        self,
        *,
        command_id: int,
        baseline_state: dict[str, Any] | None = None,
        media_type: str = "",
        expected_title: str = "",
        expected_artist: str = "",
        expected_album: str = "",
        allow_paused: bool = True,
    ) -> tuple[str, bool]:
        last = ""
        normalized_media_type = str(media_type).strip().lower()
        max_attempts = 12 if normalized_media_type in {"album", "playlist"} else 8
        for offset in range(max_attempts):
            if offset:
                time.sleep(0.5)
            timeline = self._timeline(include_metadata=True, command_id=command_id + offset)
            last = timeline
            state = self._extract_timeline_state(timeline)
            playable_states = {"playing", "buffering"} if not allow_paused else {"playing", "paused", "buffering"}
            if state.get("state") not in playable_states:
                continue
            if self._playback_state_matches_expected(
                state,
                expected_title=expected_title,
                expected_artist=expected_artist,
                expected_album=expected_album,
            ):
                return timeline, True
            if expected_title or expected_artist or expected_album:
                continue
            if self._playback_state_changed_from_baseline(state, baseline_state):
                return timeline, True
        return last, False

    def _poll_until_stopped(
        self,
        *,
        command_id: int,
    ) -> tuple[str, bool]:
        last = ""
        for offset in range(8):
            if offset:
                time.sleep(0.5)
            timeline = self._timeline(include_metadata=True, command_id=command_id + offset)
            last = timeline
            state = self._extract_timeline_state(timeline)
            if state.get("state") not in {"playing", "paused", "buffering"}:
                return timeline, True
        return last, False

    def _poll_until_volume(self, expected_level: int, *, command_id: int) -> int | None:
        last_level: int | None = None
        for offset in range(8):
            if offset:
                time.sleep(0.25)
            timeline = self._timeline(include_metadata=True, command_id=command_id + offset)
            state = self._extract_timeline_state(timeline)
            current_level = safe_int(state.get("volume"))
            last_level = current_level
            if current_level == expected_level:
                return current_level
        return last_level

    def _playback_state_matches_expected(
        self,
        state: dict[str, Any],
        *,
        expected_title: str,
        expected_artist: str,
        expected_album: str,
    ) -> bool:
        if expected_artist:
            current_artist = str(state.get("artist", "")).strip().lower()
            if current_artist == str(expected_artist).strip().lower():
                return True
        if expected_title:
            current_title = str(state.get("title", "")).strip().lower()
            if current_title == str(expected_title).strip().lower():
                return True
        if expected_album:
            current_album = str(state.get("album", "")).strip().lower()
            if current_album == str(expected_album).strip().lower():
                return True
        return False

    def _playback_state_changed_from_baseline(
        self,
        state: dict[str, Any],
        baseline_state: dict[str, Any] | None,
    ) -> bool:
        if not baseline_state:
            return True
        previous_playing = bool(baseline_state.get("playing"))
        current_state = str(state.get("state", "")).strip().lower()
        if not previous_playing and current_state in {"playing", "paused", "buffering"}:
            return True

        previous_title = str(baseline_state.get("title", "")).strip().lower()
        previous_artist = str(baseline_state.get("artist", "")).strip().lower()
        previous_album = str(baseline_state.get("album", "")).strip().lower()
        current_title = str(state.get("title", "")).strip().lower()
        current_artist = str(state.get("artist", "")).strip().lower()
        current_album = str(state.get("album", "")).strip().lower()
        return (current_title, current_artist, current_album) != (previous_title, previous_artist, previous_album)

    def _request(self, path: str, params: dict[str, Any], *, expect_body: bool = False) -> CommandResult:
        query = parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
        endpoint = f"{self.base_url}{path}"
        if query:
            endpoint = f"{endpoint}?{query}"
        req = request.Request(endpoint, method="GET")
        try:
            with request.urlopen(req, timeout=self.http_timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload = {"raw_xml": raw} if expect_body and raw else None
                return CommandResult(ok=True, state="accepted", payload=payload)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return CommandResult(ok=False, state="failed", detail=detail or f"HTTP {exc.code}")
        except error.URLError as exc:
            return CommandResult(ok=False, state="failed", detail=str(exc.reason))

    def _extract_timeline_state(self, payload: str) -> dict[str, Any]:
        root = ElementTree.fromstring(payload)
        for timeline in root.findall("Timeline"):
            if timeline.attrib.get("type") != "music":
                continue
            track = timeline.find("Track")
            track_attrib = track.attrib if track is not None else {}
            state = {
                "state": timeline.attrib.get("state", "unknown"),
                "volume": safe_int(timeline.attrib.get("volume")),
                "type": "",
                "plex_key": "",
                "title": "",
                "artist": "",
                "album": "",
            }
            state["type"] = (
                timeline.attrib.get("itemType")
                or timeline.attrib.get("type")
                or track_attrib.get("type")
                or "track"
            )
            state["plex_key"] = (
                timeline.attrib.get("key")
                or track_attrib.get("key")
                or ""
            )
            state["title"] = timeline.attrib.get("title") or track_attrib.get("title", "")
            state["artist"] = (
                timeline.attrib.get("grandparentTitle")
                or timeline.attrib.get("originalTitle")
                or track_attrib.get("originalTitle")
                or track_attrib.get("grandparentTitle")
                or ""
            )
            state["album"] = timeline.attrib.get("parentTitle") or track_attrib.get("parentTitle", "")
            return state
        return {"state": "unknown", "title": "", "artist": "", "album": ""}

    def _build_playback_uri(self, plex_key: str) -> str:
        return f"server://{self.plex_machine_identifier}/com.plexapp.plugins.library{plex_key}"

    def _music_active_for_longform_start(self, now_playing: dict[str, Any] | None) -> bool:
        if not isinstance(now_playing, dict):
            return False
        current_state = str(now_playing.get("state", "")).strip().lower()
        return bool(now_playing.get("playing")) or current_state in {"playing", "paused", "buffering", "starting", "stopping"}

    def _resume_snapshot_from_state(self, state: dict[str, Any] | None) -> PausedPlaybackSnapshot | None:
        if not state:
            return None
        plex_key = str(state.get("plex_key", "")).strip()
        media_type = str(state.get("type", "")).strip().lower() or "track"
        if not plex_key:
            return None
        return PausedPlaybackSnapshot(
            media_type=media_type,
            plex_key=plex_key,
            title=str(state.get("title", "")).strip(),
            artist=str(state.get("artist", "")).strip(),
            album=str(state.get("album", "")).strip(),
            stored_at=time.time(),
        )

    def _fresh_paused_snapshot(self) -> PausedPlaybackSnapshot | None:
        snapshot = self._paused_snapshot
        if snapshot is None:
            return None
        if (time.time() - snapshot.stored_at) > self._paused_snapshot_max_age_seconds:
            self._paused_snapshot = None
            return None
        return snapshot


PlexampHttpAdapter = LocalPlaybackAdapter
