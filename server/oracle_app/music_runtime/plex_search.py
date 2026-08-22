from __future__ import annotations

from typing import Any

from oracle_app.music_runtime.matching import build_query_variants, build_search_queries, normalize_match_text
from oracle_app.music_runtime.parsing import MusicIntent


def search_plex_catalog(bridge, intent: MusicIntent) -> list[dict[str, Any]]:
    media_types = [intent.media_type] if intent.media_type else ["track", "album", "artist", "playlist"]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in build_search_queries(intent):
        for media_type in media_types:
            _append_unique(results, seen, bridge.search_media(media_type, query))
    if intent.media_type == "track" and intent.title and intent.album:
        _append_unique(results, seen, search_track_from_album(bridge, intent))
    if intent.media_type == "track" and intent.title and intent.artist:
        _append_unique(results, seen, search_track_from_artist(bridge, intent))
    return results


def search_track_from_album(bridge, intent: MusicIntent) -> list[dict[str, Any]]:
    album_query = str(intent.album or "").strip()
    title_target = normalize_match_text(intent.title or "")
    if not album_query or not title_target:
        return []
    albums: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in build_query_variants(album_query):
        _append_unique(albums, seen, bridge.search_media("album", query))
    matches: list[dict[str, Any]] = []
    for album in albums[:5]:
        for track in bridge.children(str(album.get("plex_key") or ""), media_type="track"):
            if _title_matches(track, title_target):
                if not track.get("album"):
                    track["album"] = album.get("title", "")
                if not track.get("artist"):
                    track["artist"] = album.get("artist", "")
                matches.append(track)
    return matches


def search_track_from_artist(bridge, intent: MusicIntent) -> list[dict[str, Any]]:
    artist_query = str(intent.artist or "").strip()
    title_target = normalize_match_text(intent.title or "")
    if not artist_query or not title_target:
        return []
    artists: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in build_query_variants(artist_query):
        _append_unique(artists, seen, bridge.search_media("artist", query))
    matches: list[dict[str, Any]] = []
    track_seen: set[str] = set()
    for artist in artists[:5]:
        artist_key = str(artist.get("plex_key") or "")
        direct = bridge.children(artist_key, media_type="track")
        collections = [direct] if direct else [
            bridge.children(str(album.get("plex_key") or ""), media_type="track")
            for album in bridge.children(artist_key, media_type="album")[:8]
        ]
        for tracks in collections:
            for track in tracks:
                identity = _identity(track)
                if identity and identity not in track_seen and _title_matches(track, title_target):
                    if not track.get("artist"):
                        track["artist"] = str(artist.get("title") or "")
                    track_seen.add(identity)
                    matches.append(track)
    return matches


def _append_unique(target: list[dict[str, Any]], seen: set[str], items: list[dict[str, Any]]) -> None:
    for item in items:
        identity = _identity(item)
        if identity and identity not in seen:
            seen.add(identity)
            target.append(item)


def _identity(item: dict[str, Any]) -> str:
    return str(item.get("rating_key") or item.get("plex_key") or "").strip()


def _title_matches(track: dict[str, Any], target: str) -> bool:
    candidate = normalize_match_text(track.get("title", ""))
    return bool(candidate and (candidate == target or target in candidate or candidate in target))
