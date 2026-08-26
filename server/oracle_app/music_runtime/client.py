"""Canonical-only music helpers with explicit provider settings."""

from __future__ import annotations

from typing import Any

from oracle_app.provider_bridges.plex_music import PlexMusicBridge

from .parsing import MusicIntent
from .plex_search import search_track_from_album, search_track_from_artist


def search_track_from_album_fallback(
    intent: MusicIntent,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    return search_track_from_album(PlexMusicBridge(settings), intent)


def search_track_from_artist_fallback(
    intent: MusicIntent,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    return search_track_from_artist(PlexMusicBridge(settings), intent)


def build_native_queue_manifest(
    selection: dict[str, Any],
    *,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    return PlexMusicBridge(settings).build_native_queue_manifest(selection)
