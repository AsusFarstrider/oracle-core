from __future__ import annotations

from typing import Any

from oracle_app.config import get_music_settings
from oracle_app.provider_bridges import get_music_bridge

from .parsing import MusicIntent
from .plex_search import search_plex_catalog, search_track_from_album, search_track_from_artist


def _bridge():
    return get_music_bridge(get_music_settings())


def search_plex(intent: MusicIntent) -> list[dict[str, Any]]:
    return search_plex_catalog(_bridge(), intent)


def build_native_queue_manifest(selection: dict[str, Any]) -> dict[str, Any] | None:
    return _bridge().build_native_queue_manifest(selection)


def search_track_from_album_fallback(intent: MusicIntent, settings: dict[str, Any]) -> list[dict[str, Any]]:
    return search_track_from_album(_bridge(), intent)


def search_track_from_artist_fallback(intent: MusicIntent, settings: dict[str, Any]) -> list[dict[str, Any]]:
    return search_track_from_artist(_bridge(), intent)


def build_library_search_endpoint(settings: dict[str, Any], media_type: str, query: str) -> str:
    return _bridge().build_library_search_endpoint(settings, media_type, query)


def build_playlist_search_endpoint(settings: dict[str, Any], query: str) -> str:
    return _bridge().build_playlist_search_endpoint(settings, query)


def build_metadata_children_endpoint(settings: dict[str, Any], plex_key: str) -> str:
    return _bridge().build_metadata_children_endpoint(settings, plex_key)


def fetch_xml(endpoint: str, settings: dict[str, Any]) -> str:
    return _bridge().fetch_xml(endpoint, settings)


def extract_xml_results(payload: str, *, media_type: str) -> list[dict[str, Any]]:
    return _bridge().extract_xml_results(payload, media_type=media_type)


def safe_int(value: Any) -> int:
    return _bridge().safe_int(value)
