from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias
from urllib import error, parse, request
from xml.etree import ElementTree

from oracle_app.config import get_music_settings
from oracle_app.music_runtime.selection import music_provider_ref, music_selection_id


class MusicBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class MusicBridgeConfigurationError(MusicBridgeError):
    pass


@dataclass(frozen=True)
class PlexMusicProviderConnection:
    base_url: str
    credential: str = field(repr=False)
    timeout_seconds: int
    music_section_id: int
    machine_identifier: str | None = None


MusicProviderSettings: TypeAlias = dict[str, Any] | PlexMusicProviderConnection


class PlexMusicBridge:
    provider_name = "plex"

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        *,
        connection: PlexMusicProviderConnection | None = None,
    ) -> None:
        if settings is not None and connection is not None:
            raise ValueError("Plex bridge accepts either legacy settings or one typed connection.")
        self._configured_settings = settings
        self._connection = connection

    def search_media(self, media_type: str, query: str) -> list[dict[str, Any]]:
        settings = self._settings()
        self._validate_settings(settings)
        endpoint = (
            self.build_playlist_search_endpoint(settings, query)
            if media_type == "playlist"
            else self.build_library_search_endpoint(settings, media_type, query)
        )
        return self.extract_xml_results(self.fetch_xml(endpoint, settings), media_type=media_type)

    def children(self, provider_path: str, *, media_type: str) -> list[dict[str, Any]]:
        settings = self._settings()
        self._validate_settings(settings)
        endpoint = self.build_metadata_children_endpoint(settings, provider_path)
        if not endpoint:
            return []
        return self.extract_xml_results(self.fetch_xml(endpoint, settings), media_type=media_type)

    def build_native_queue_manifest(self, selection: dict[str, Any]) -> dict[str, Any] | None:
        settings = self._settings()
        self._validate_settings(settings)

        media_type = str(selection.get("media_type") or selection.get("type") or "").strip().lower()
        if media_type not in {"track", "album", "artist", "playlist"}:
            return None

        if media_type == "track":
            track = self._normalize_queue_track(selection)
            if track is None:
                return None
            tracks = [track]
        else:
            tracks = self._load_collection_tracks(selection, settings, media_type=media_type)
            if not tracks:
                return None

        queue_id = str(selection.get("rating_key") or selection.get("plex_key") or "").strip()
        if not queue_id and tracks:
            queue_id = str(tracks[0].get("rating_key") or tracks[0].get("plex_key") or "").strip()
        collection_title = self._resolve_collection_title(selection, media_type=media_type, tracks=tracks)
        return {
            "queue_id": queue_id,
            "queue_position": 1,
            "queue_count": len(tracks),
            "collection_title": collection_title,
            "collection_type": media_type,
            "tracks": tracks,
        }

    def build_library_search_endpoint(self, settings: MusicProviderSettings, media_type: str, query: str) -> str:
        plex_type = {
            "artist": 8,
            "album": 9,
            "track": 10,
        }.get(media_type, 10)
        params = parse.urlencode(
            {
                "type": plex_type,
                "query": query,
                "X-Plex-Token": _provider_credential(settings),
            }
        )
        return f"{_provider_base_url(settings)}/library/sections/{_provider_music_section_id(settings)}/search?{params}"

    def build_playlist_search_endpoint(self, settings: MusicProviderSettings, query: str) -> str:
        params = parse.urlencode(
            {
                "playlistType": "audio",
                "query": query,
                "X-Plex-Token": _provider_credential(settings),
            }
        )
        return f"{_provider_base_url(settings)}/playlists/all?{params}"

    def build_metadata_children_endpoint(self, settings: MusicProviderSettings, plex_key: str) -> str:
        key = str(plex_key).strip()
        if not key:
            return ""
        separator = "&" if "?" in key else "?"
        return f"{_provider_base_url(settings)}{key}{separator}X-Plex-Token={_provider_credential(settings)}"

    def build_sessions_endpoint(self, settings: MusicProviderSettings) -> str:
        params = parse.urlencode({"X-Plex-Token": _provider_credential(settings)})
        return f"{_provider_base_url(settings)}/status/sessions?{params}"

    def get_active_sessions_status(self) -> dict[str, Any]:
        settings = self._settings()
        self._validate_settings(settings)
        payload = self.fetch_xml(self.build_sessions_endpoint(settings), settings)
        return self.extract_active_sessions_status(payload)

    def fetch_artwork(self, path: str):
        settings = self._settings()
        self._validate_settings(settings)
        normalized = str(path or "").strip()
        if not normalized.startswith("/") or "://" in normalized:
            raise MusicBridgeError("music_artwork_path_invalid", "Invalid Plex artwork path")
        endpoint = f"{_provider_base_url(settings)}{normalized}"
        separator = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{separator}X-Plex-Token={parse.quote(_provider_credential(settings))}"
        req = request.Request(endpoint, method="GET")
        try:
            return request.urlopen(req, timeout=_provider_timeout_seconds(settings))
        except (error.HTTPError, error.URLError) as exc:
            raise MusicBridgeError("music_artwork_unavailable", str(exc)) from exc

    def extract_active_sessions_status(self, payload: str) -> dict[str, Any]:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise MusicBridgeError("music_provider_invalid_response", "Plex sessions returned invalid XML") from exc

        sessions: list[dict[str, str]] = []
        for item in root:
            media_type = item.tag.lower()
            if media_type not in {"video", "track", "photo"}:
                continue
            sessions.append(
                {
                    "type": media_type,
                    "title": str(item.attrib.get("title") or "").strip(),
                    "user": str(item.attrib.get("user") or "").strip(),
                    "player": _plex_child_attribute(item, "Player", "title"),
                }
            )
        declared_size = self.safe_int(root.attrib.get("size"))
        active_count = max(declared_size, len(sessions))
        return {
            "provider": self.provider_name,
            "available": True,
            "active_stream_count": active_count,
            "sessions": sessions,
        }

    def fetch_xml(self, endpoint: str, settings: MusicProviderSettings) -> str:
        req = request.Request(endpoint, method="GET")
        timeout_seconds = _provider_timeout_seconds(settings)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MusicBridgeError("music_provider_request_failed", detail or f"Plex request returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise MusicBridgeError("music_provider_request_failed", str(exc.reason)) from exc

    def extract_xml_results(self, payload: str, *, media_type: str) -> list[dict[str, Any]]:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise MusicBridgeError("music_provider_invalid_response", "Plex search returned invalid XML") from exc

        results: list[dict[str, Any]] = []
        for item in root:
            element_type = item.tag.lower()
            if element_type == "directory":
                normalized_type = str(item.attrib.get("type", "")).strip().lower()
            elif element_type == "track":
                normalized_type = "track"
            elif element_type == "playlist":
                normalized_type = "playlist"
            else:
                continue

            if normalized_type not in {"track", "album", "artist", "playlist"}:
                continue
            if media_type != "playlist" and normalized_type != media_type:
                continue

            title = str(item.attrib.get("title", "")).strip()
            artist = str(
                item.attrib.get("originalTitle")
                or item.attrib.get("grandparentTitle")
                or item.attrib.get("parentTitle")
                or item.attrib.get("title")
                or ""
            ).strip()
            if normalized_type == "artist":
                artist = title
            album = str(item.attrib.get("parentTitle") or item.attrib.get("title") or "").strip()
            if normalized_type == "album":
                album = title
            parent_key = str(item.attrib.get("parentKey") or "").strip()
            if normalized_type == "track" and not parent_key and item.attrib.get("parentRatingKey"):
                parent_key = f"/library/metadata/{item.attrib['parentRatingKey']}/children"
            art_path = str(
                item.attrib.get("thumb")
                or item.attrib.get("parentThumb")
                or item.attrib.get("grandparentThumb")
                or item.attrib.get("art")
                or ""
            ).strip()
            normalized = {
                    "type": normalized_type,
                    "title": title,
                    "artist": artist if normalized_type != "playlist" else "",
                    "album": album if normalized_type != "artist" else "",
                    "plex_key": str(item.attrib.get("key", "")).strip(),
                    "parent_key": parent_key,
                    "rating_key": str(item.attrib.get("ratingKey", "")).strip(),
                    "art_path": art_path,
                    "index": self.safe_int(item.attrib.get("index")),
                    "parent_index": self.safe_int(item.attrib.get("parentIndex")),
                    "duration_seconds": (
                        float(item.attrib.get("duration")) / 1000.0
                        if str(item.attrib.get("duration", "")).strip().isdigit()
                        else 0.0
                    ),
                }
            normalized["provider_item_id"] = normalized["rating_key"]
            normalized["provider_item_path"] = normalized["plex_key"]
            normalized["provider_parent_path"] = normalized["parent_key"]
            normalized["provider_ref"] = music_provider_ref(normalized)
            normalized["selection_id"] = music_selection_id(normalized)
            results.append(normalized)

        return results

    def safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _load_collection_tracks(
        self,
        selection: dict[str, Any],
        settings: MusicProviderSettings,
        *,
        media_type: str,
    ) -> list[dict[str, Any]]:
        plex_key = str(selection.get("plex_key") or "").strip()
        if not plex_key:
            return []
        children_payload = self.fetch_xml(self.build_metadata_children_endpoint(settings, plex_key), settings)
        if media_type in {"album", "playlist"}:
            return self._normalize_and_sort_tracks(
                self.extract_xml_results(children_payload, media_type="track"),
                fallback_artist=str(selection.get("artist", "")).strip(),
                fallback_album=str(selection.get("album") or selection.get("title") or "").strip(),
            )
        if media_type != "artist":
            return []

        direct_tracks = self.extract_xml_results(children_payload, media_type="track")
        if direct_tracks:
            return self._normalize_and_sort_tracks(
                direct_tracks,
                fallback_artist=str(selection.get("artist") or selection.get("title") or "").strip(),
                fallback_album="",
            )

        albums = self.extract_xml_results(children_payload, media_type="album")
        tracks: list[dict[str, Any]] = []
        fallback_artist = str(selection.get("artist") or selection.get("title") or "").strip()
        for album in albums:
            album_key = str(album.get("plex_key", "")).strip()
            if not album_key:
                continue
            album_children_payload = self.fetch_xml(self.build_metadata_children_endpoint(settings, album_key), settings)
            album_tracks = self.extract_xml_results(album_children_payload, media_type="track")
            tracks.extend(
                self._normalize_and_sort_tracks(
                    album_tracks,
                    fallback_artist=fallback_artist,
                    fallback_album=str(album.get("album") or album.get("title") or "").strip(),
                )
            )
        return tracks

    def _normalize_and_sort_tracks(
        self,
        tracks: list[dict[str, Any]],
        *,
        fallback_artist: str,
        fallback_album: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for track in tracks:
            normalized_track = self._normalize_queue_track(
                track,
                fallback_artist=fallback_artist,
                fallback_album=fallback_album,
            )
            if normalized_track is not None:
                normalized.append(normalized_track)
        normalized.sort(
            key=lambda item: (
                self._sort_number(item.get("parent_index")),
                self._sort_number(item.get("index")),
                str(item.get("album", "")).strip().lower(),
                str(item.get("title", "")).strip().lower(),
            )
        )
        return normalized

    def _normalize_queue_track(
        self,
        track: dict[str, Any],
        *,
        fallback_artist: str = "",
        fallback_album: str = "",
    ) -> dict[str, Any] | None:
        track_id = str(track.get("rating_key") or track.get("plex_key") or "").strip()
        plex_key = str(track.get("plex_key") or "").strip()
        title = str(track.get("title", "")).strip()
        if not track_id or not plex_key or not title:
            return None
        artist = str(track.get("artist") or fallback_artist or "").strip()
        album = str(track.get("album") or fallback_album or "").strip()
        try:
            duration_seconds = float(track.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        normalized = {
            "rating_key": track_id,
            "plex_key": plex_key,
            "parent_key": str(track.get("parent_key") or "").strip(),
            "title": title,
            "artist": artist,
            "album": album,
            "duration_seconds": max(0.0, duration_seconds),
            "index": self._sort_number(track.get("index")),
            "parent_index": self._sort_number(track.get("parent_index")),
        }
        normalized["provider_ref"] = music_provider_ref(normalized)
        normalized["selection_id"] = music_selection_id(normalized)
        return normalized

    def _resolve_collection_title(
        self,
        selection: dict[str, Any],
        *,
        media_type: str,
        tracks: list[dict[str, Any]],
    ) -> str:
        if media_type == "track":
            return str(selection.get("album") or selection.get("title") or "").strip()
        if media_type == "artist":
            return str(selection.get("artist") or selection.get("title") or "").strip()
        title = str(selection.get("album") or selection.get("title") or "").strip()
        if title:
            return title
        if tracks:
            return str(tracks[0].get("album") or tracks[0].get("title") or "").strip()
        return ""

    def _sort_number(self, value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _validate_settings(self, settings: MusicProviderSettings) -> None:
        if isinstance(settings, PlexMusicProviderConnection):
            if settings.base_url and settings.credential:
                return
        elif settings.get("plex_configured"):
            return
        raise MusicBridgeConfigurationError("music_provider_not_configured", "Plex is not configured")

    def _settings(self) -> MusicProviderSettings:
        return self._connection or self._configured_settings or get_music_settings()


def get_music_bridge(settings: dict[str, Any] | None = None) -> PlexMusicBridge:
    provider = str((settings or {}).get("music_provider") or "plex").strip().lower()
    if provider == "plex":
        return PlexMusicBridge(settings)
    raise MusicBridgeConfigurationError("music_provider_unsupported", f"Unsupported music provider: {provider}")


def _provider_base_url(settings: MusicProviderSettings) -> str:
    return settings.base_url if isinstance(settings, PlexMusicProviderConnection) else str(settings["plex_base_url"])


def _provider_credential(settings: MusicProviderSettings) -> str:
    return settings.credential if isinstance(settings, PlexMusicProviderConnection) else str(settings["plex_token"])


def _provider_timeout_seconds(settings: MusicProviderSettings) -> int:
    if isinstance(settings, PlexMusicProviderConnection):
        return settings.timeout_seconds
    return int(settings.get("plex_timeout_seconds") or 10)


def _provider_music_section_id(settings: MusicProviderSettings) -> int:
    if isinstance(settings, PlexMusicProviderConnection):
        return settings.music_section_id
    return int(settings["plex_music_section_id"])


def _plex_child_attribute(element: ElementTree.Element, child_tag: str, attribute_name: str) -> str:
    child = element.find(child_tag)
    if child is None:
        return ""
    return str(child.attrib.get(attribute_name) or "").strip()
