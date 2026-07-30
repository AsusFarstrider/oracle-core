from __future__ import annotations

from typing import Any

from oracle_app.config import get_satellite_music_backend_hint as default_get_satellite_music_backend_hint
from oracle_app.music_runtime.client import build_native_queue_manifest as default_build_native_queue_manifest
from oracle_app.music_runtime.selection import (
    music_provider_ref,
    music_selection_id,
    music_selection_with_provider_fields,
)


def music_playback_selection(selection: dict[str, Any]) -> dict[str, Any]:
    media_type = str(selection.get("media_type") or selection.get("type") or "").strip()
    return {
        "selection_id": music_selection_id(selection),
        "provider_ref": music_provider_ref(selection),
        "media_type": media_type,
        "type": media_type,
        "title": selection.get("title"),
        "artist": selection.get("artist"),
        "album": selection.get("album"),
        "duration_seconds": selection.get("duration_seconds"),
        "score": selection.get("score"),
    }


def build_music_play_media_args(
    source: str | None,
    selection: dict[str, Any],
    *,
    get_backend_hint=default_get_satellite_music_backend_hint,
    build_manifest=default_build_native_queue_manifest,
) -> dict[str, Any]:
    provider_selection = music_selection_with_provider_fields(selection)
    media_type = str(provider_selection.get("media_type") or provider_selection.get("type") or "").strip()
    backend_hint = get_backend_hint(source, media_type=media_type)
    args = {
        "media_type": media_type,
        "plex_key": provider_selection.get("plex_key"),
        "parent_key": provider_selection.get("parent_key"),
        "rating_key": provider_selection.get("rating_key"),
        "title": provider_selection.get("title"),
        "artist": provider_selection.get("artist"),
        "album": provider_selection.get("album"),
        "duration_seconds": float(provider_selection.get("duration_seconds") or 0.0),
        "backend_hint": backend_hint,
    }
    if backend_hint != "oracle_native_music":
        return args

    manifest = build_manifest(provider_selection)
    if not isinstance(manifest, dict):
        return args
    tracks = manifest.get("tracks")
    if isinstance(tracks, list) and tracks:
        first_track = music_selection_with_provider_fields(tracks[0]) if isinstance(tracks[0], dict) else {}
        args["plex_key"] = first_track.get("plex_key", args["plex_key"])
        args["parent_key"] = first_track.get("parent_key", args["parent_key"])
        args["rating_key"] = first_track.get("rating_key", args["rating_key"])
        args["title"] = first_track.get("title", args["title"])
        args["artist"] = first_track.get("artist", args["artist"])
        args["album"] = first_track.get("album", args["album"])
        args["duration_seconds"] = float(first_track.get("duration_seconds") or args["duration_seconds"] or 0.0)
        args["queue_tracks"] = tracks
    args["queue_id"] = manifest.get("queue_id")
    args["queue_position"] = manifest.get("queue_position")
    args["queue_count"] = manifest.get("queue_count")
    args["collection_title"] = manifest.get("collection_title")
    args["collection_type"] = manifest.get("collection_type")
    return args
