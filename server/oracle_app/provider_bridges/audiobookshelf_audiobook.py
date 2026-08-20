from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from oracle_app.audiobook_runtime.matching import build_search_queries
from oracle_app.config import get_audiobook_connection_settings


class AudiobookBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str, *, http_status: int | None = None) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail
        self.http_status = http_status


class AudiobookBridgeConfigurationError(AudiobookBridgeError):
    pass


@dataclass(frozen=True)
class AudiobookProviderConnection:
    base_url: str
    library_id: str
    api_key: str
    timeout_seconds: int
    configured: bool = True
    user_id: str | None = None
    user_enabled: bool = True


class AudiobookshelfAudiobookBridge:
    provider_name = "audiobookshelf"

    def __init__(
        self,
        connection_resolver: Callable[[str | None], AudiobookProviderConnection] | None = None,
    ) -> None:
        self._connection_resolver = connection_resolver or _legacy_connection

    def search_titles(
        self,
        query: str,
        narrator_preference: str | None = None,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        settings = self._connection_resolver(user_id)
        self._validate_user_audiobook_settings(settings)

        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for search_query in build_search_queries(query, narrator_preference):
            payload = self.request_json(
                f"/api/libraries/{settings.library_id}/search?q={parse.quote(search_query)}",
                method="GET",
                user_id=user_id,
            )
            results = payload.get("book")
            if not isinstance(results, list):
                continue
            for item in results:
                candidate = self._normalize_search_candidate(item)
                library_item_id = str(candidate.get("library_item_id", "")).strip()
                if not library_item_id or library_item_id in seen_ids:
                    continue
                candidates.append(candidate)
                seen_ids.add(library_item_id)
        return [item for item in candidates if item.get("library_item_id")]

    def fetch_item(self, library_item_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        payload = self.request_json(
            f"/api/items/{parse.quote(library_item_id)}?expanded=1&include=progress",
            method="GET",
            user_id=user_id,
        )
        return self._normalize_item(payload, fallback_id=library_item_id)

    def fetch_current_progress(self, *, user_id: str | None = None) -> dict[str, Any] | None:
        payload = self.request_json("/api/me", method="GET", user_id=user_id)
        progress_entries = payload.get("mediaProgress")
        if not isinstance(progress_entries, list):
            return None

        current: dict[str, Any] | None = None
        for entry in progress_entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("mediaItemType", "")).strip() != "book":
                continue
            if bool(entry.get("isFinished")):
                continue
            last_update = int(entry.get("lastUpdate") or 0)
            current_time = float(entry.get("currentTime") or 0)
            if current_time <= 0:
                continue
            if current is None or last_update > int(current.get("lastUpdate") or 0):
                current = entry
        return self._normalize_progress(current) if current is not None else None

    def open_playback_session(
        self,
        library_item_id: str,
        *,
        supported_mime_types: list[str],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "deviceInfo": {
                "deviceId": "oracle-brain",
                "clientName": "Oracle",
                "clientVersion": "0.1",
                "manufacturer": "Oracle",
                "model": "brain",
            },
            "forceDirectPlay": True,
            "supportedMimeTypes": list(supported_mime_types),
        }
        session = self.request_json(
            f"/api/items/{parse.quote(library_item_id)}/play",
            method="POST",
            payload=payload,
            user_id=user_id,
        )
        return self._normalize_playback_session(session, fallback_library_item_id=library_item_id)

    def _normalize_progress(self, progress: dict[str, Any]) -> dict[str, Any]:
        metadata = progress.get("mediaMetadata") if isinstance(progress.get("mediaMetadata"), dict) else {}
        authors = metadata.get("authors") or []
        author = ", ".join(
            str(item.get("name") or "").strip()
            for item in authors
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        return {
            "library_item_id": str(progress.get("libraryItemId") or "").strip(),
            "media_kind": str(progress.get("mediaItemType") or "").strip(),
            "current_time_seconds": float(progress.get("currentTime") or 0),
            "duration_seconds": float(progress.get("duration") or 0),
            "last_update_epoch_ms": int(progress.get("lastUpdate") or 0),
            "finished": bool(progress.get("isFinished")),
            "title": str(metadata.get("title") or progress.get("displayTitle") or "").strip(),
            "author": author,
        }

    def _normalize_item(self, item: dict[str, Any], *, fallback_id: str) -> dict[str, Any]:
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        metadata = media.get("metadata") if isinstance(media.get("metadata"), dict) else {}
        authors = metadata.get("authors") or []
        author = ", ".join(
            str(value.get("name") or "").strip()
            for value in authors
            if isinstance(value, dict) and str(value.get("name") or "").strip()
        )
        progress = item.get("userMediaProgress") if isinstance(item.get("userMediaProgress"), dict) else None
        return {
            "library_item_id": str(item.get("id") or fallback_id).strip(),
            "title": str(metadata.get("title") or item.get("title") or "").strip(),
            "author": author or str(metadata.get("authorName") or "").strip(),
            "duration_seconds": float(media.get("duration") or 0),
            "progress": self._normalize_progress(progress) if progress is not None else None,
        }

    def _normalize_playback_session(
        self,
        session: dict[str, Any],
        *,
        fallback_library_item_id: str,
    ) -> dict[str, Any]:
        metadata = session.get("mediaMetadata") if isinstance(session.get("mediaMetadata"), dict) else {}
        authors = metadata.get("authors") or []
        author = ", ".join(
            str(item.get("name") or "").strip()
            for item in authors
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        tracks = []
        for track in session.get("audioTracks") or []:
            if not isinstance(track, dict):
                continue
            tracks.append(
                {
                    "content_url": str(track.get("contentUrl") or "").strip(),
                    "mime_type": str(track.get("mimeType") or "").strip(),
                    "duration_seconds": float(track.get("duration") or 0),
                    "start_offset_seconds": float(track.get("startOffset") or 0),
                    "title": str(track.get("title") or "").strip(),
                }
            )
        chapters = []
        for chapter in session.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            chapters.append(
                {
                    "title": str(chapter.get("title") or "").strip(),
                    "start_seconds": float(chapter.get("start") or 0),
                    "end_seconds": float(chapter.get("end") or 0),
                }
            )
        return {
            "provider_session_id": str(session.get("id") or "").strip(),
            "library_item_id": str(session.get("libraryItemId") or fallback_library_item_id).strip(),
            "title": str(session.get("displayTitle") or "").strip(),
            "author": author or str(session.get("displayAuthor") or "").strip(),
            "duration_seconds": float(session.get("duration") or 0),
            "current_time_seconds": float(session.get("currentTime") or session.get("startTime") or 0),
            "tracks": tracks,
            "chapters": chapters,
        }

    def sync_session(
        self,
        session_id: str,
        *,
        current_time: float,
        time_listened: float,
        duration: float,
        user_id: str | None = None,
    ) -> None:
        self.request_text(
            f"/api/session/{parse.quote(session_id)}/sync",
            method="POST",
            payload={
                "currentTime": current_time,
                "timeListened": max(0.0, time_listened),
                "duration": duration,
            },
            user_id=user_id,
        )

    def close_session(
        self,
        session_id: str,
        *,
        current_time: float,
        time_listened: float,
        duration: float,
        user_id: str | None = None,
    ) -> None:
        self.request_text(
            f"/api/session/{parse.quote(session_id)}/close",
            method="POST",
            payload={
                "currentTime": current_time,
                "timeListened": max(0.0, time_listened),
                "duration": duration,
            },
            user_id=user_id,
        )

    def fetch_stream(
        self,
        playback: dict[str, Any],
        track_index: int,
        *,
        range_header: str | None = None,
    ):
        tracks = playback.get("tracks")
        if not isinstance(tracks, list) or track_index < 0 or track_index >= len(tracks):
            raise AudiobookBridgeError("audiobook_track_not_found", "Audiobook track not found", http_status=404)
        track = tracks[track_index]
        if not isinstance(track, dict):
            raise AudiobookBridgeError("audiobook_track_not_found", "Audiobook track not found", http_status=404)

        relative_url = str(track.get("content_url", "")).strip()
        if not relative_url:
            raise AudiobookBridgeError("audiobook_track_not_found", "Audiobook track not found", http_status=404)

        settings = self._connection_resolver(str(playback.get("user_id") or "").strip() or None)
        self._validate_user_audiobook_settings(settings)

        endpoint = f"{settings.base_url}{relative_url}"
        headers = {"Authorization": f"Bearer {settings.api_key}"}
        if range_header:
            headers["Range"] = range_header

        req = request.Request(endpoint, headers=headers, method="GET")
        try:
            return request.urlopen(req, timeout=settings.timeout_seconds)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AudiobookBridgeError(
                "audiobook_provider_request_failed",
                detail or "Audiobookshelf returned an error",
                http_status=exc.code,
            ) from exc
        except error.URLError as exc:
            raise AudiobookBridgeError("audiobook_provider_unreachable", str(exc.reason), http_status=502) from exc

    def request_json(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        response = self.request_raw(path, method=method, payload=payload, user_id=user_id)
        raw = response.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AudiobookBridgeError("audiobook_provider_invalid_response", "Audiobookshelf returned invalid JSON") from exc

    def request_text(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> str:
        response = self.request_raw(path, method=method, payload=payload, user_id=user_id)
        return response.read().decode("utf-8", errors="replace")

    def request_raw(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        user_id: str | None = None,
    ):
        settings = self._connection_resolver(user_id)
        self._validate_user_audiobook_settings(settings)

        endpoint = f"{settings.base_url}{path}"
        body = None
        headers = {"Authorization": f"Bearer {settings.api_key}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            return request.urlopen(req, timeout=settings.timeout_seconds)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AudiobookBridgeError(
                "audiobook_provider_request_failed",
                detail or f"Audiobookshelf request returned HTTP {exc.code}",
            ) from exc
        except error.URLError as exc:
            raise AudiobookBridgeError("audiobook_provider_request_failed", str(exc.reason)) from exc

    def _normalize_search_candidate(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        library_item = item.get("libraryItem")
        if not isinstance(library_item, dict):
            return {}
        library_item_id = str(library_item.get("id", "")).strip()
        media = library_item.get("media") or {}
        metadata = media.get("metadata") or {}
        authors = metadata.get("authors") or []
        author_names = [str(author.get("name", "")).strip() for author in authors if isinstance(author, dict)]
        author_text = ", ".join(name for name in author_names if name)
        series_entries = []
        for series in metadata.get("series") or []:
            if not isinstance(series, dict):
                continue
            name = str(series.get("name", "")).strip()
            sequence = str(series.get("sequence", "")).strip()
            if not name or not sequence:
                continue
            series_entries.append({"name": name, "sequence": sequence})
        return {
            "library_item_id": library_item_id,
            "title": str(metadata.get("title", "")).strip(),
            "subtitle": str(metadata.get("subtitle", "")).strip(),
            "author": author_text or str(metadata.get("authorName", "")).strip(),
            "narrator": str(metadata.get("narratorName", "")).strip(),
            "series": series_entries,
            "duration": float(media.get("duration") or 0),
        }

    def _validate_user_audiobook_settings(self, settings: AudiobookProviderConnection) -> None:
        if settings.configured:
            return
        user_id = str(settings.user_id or "").strip()
        if user_id and not settings.user_enabled:
            raise AudiobookBridgeConfigurationError("audiobook_user_disabled", f"Audiobooks are not enabled for {user_id}")
        if user_id:
            raise AudiobookBridgeConfigurationError("audiobook_user_not_configured", f"Audiobooks are not configured for {user_id}")
        raise AudiobookBridgeConfigurationError("audiobook_provider_not_configured", "Audiobookshelf is not configured")


def _legacy_connection(user_id: str | None) -> AudiobookProviderConnection:
    settings = get_audiobook_connection_settings(user_id)
    return AudiobookProviderConnection(
        base_url=str(settings.get("base_url") or ""),
        library_id=str(settings.get("library_id") or ""),
        api_key=str(settings.get("api_key") or ""),
        timeout_seconds=int(settings.get("timeout_seconds") or 10),
        configured=bool(settings.get("configured")),
        user_id=str(settings.get("user_id") or "").strip() or None,
        user_enabled=bool(settings.get("user_enabled", True)),
    )


def get_audiobook_bridge(settings: dict[str, Any] | None = None) -> AudiobookshelfAudiobookBridge:
    provider = str((settings or {}).get("audiobook_provider") or "audiobookshelf").strip().lower()
    if provider == "audiobookshelf":
        return AudiobookshelfAudiobookBridge()
    raise AudiobookBridgeConfigurationError("audiobook_provider_unsupported", f"Unsupported audiobook provider: {provider}")


def normalize_audiobook_progress(progress: dict[str, Any] | None) -> dict[str, Any] | None:
    if progress is None:
        return None
    if "library_item_id" in progress:
        return dict(progress)
    return AudiobookshelfAudiobookBridge()._normalize_progress(progress)


def normalize_audiobook_item(item: dict[str, Any], *, fallback_id: str = "") -> dict[str, Any]:
    if "progress" in item or "library_item_id" in item:
        return dict(item)
    return AudiobookshelfAudiobookBridge()._normalize_item(item, fallback_id=fallback_id)


def normalize_audiobook_playback_session(
    session: dict[str, Any],
    *,
    fallback_library_item_id: str = "",
) -> dict[str, Any]:
    if "provider_session_id" in session:
        return dict(session)
    return AudiobookshelfAudiobookBridge()._normalize_playback_session(
        session,
        fallback_library_item_id=fallback_library_item_id,
    )
