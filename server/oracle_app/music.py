from __future__ import annotations

from typing import Any

from .config import get_music_settings
from .music_runtime.canonical import CanonicalMusicExecution
from .music_runtime.client import (
    fetch_xml as _fetch_xml_via_client,
    search_plex as _search_plex_via_client,
    search_track_from_album_fallback as _search_track_from_album_fallback_via_client,
)
from .music_runtime.matching import (
    choose_music_match,
    dedupe_music_candidates,
    normalize_music_alias_text,
    normalize_music_compact_text,
    score_music_candidates,
)
from .music_runtime.ollama import (
    choose_best_guess_with_ollama,
    choose_music_match_with_ollama,
    resolve_with_ollama,
)
from .music_runtime.parsing import (
    MusicIntent,
    is_music_request,
    parse_music_intent,
)
from .music_runtime.pending import (
    looks_like_pending_music_clarification,
    match_pending_music_candidate,
)


TRANSPORT_COMMANDS = {
    "pause": "pause",
    "resume": "resume",
    "stop": "stop",
    "next": "next",
    "skip": "next",
    "previous": "previous",
    "back": "previous",
    "restart": "restart",
}


def check_music_health(
    *,
    music_execution: CanonicalMusicExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    if canonical_authority:
        configured = music_execution is not None
        return {
            "status": "ok" if configured else "disabled",
            "service": "oracle-brain",
            "plex_configured": configured,
            "configured_satellites": [] if music_execution is None else sorted(music_execution.settings.playback_targets),
            "detail": "Music routing configured" if configured else "Music is disabled in canonical configuration",
        }
    settings = get_music_settings()
    return {
        "status": "ok" if settings["plex_configured"] else "failed",
        "service": "oracle-brain",
        "plex_configured": bool(settings["plex_configured"]),
        "configured_satellites": list(settings["satellites"].keys()),
        "detail": "Music routing configured" if settings["plex_configured"] else "Plex is not configured",
    }


def search_music_catalog(intent: MusicIntent) -> list[dict[str, Any]]:
    return dedupe_music_candidates(
        _search_plex_via_client(intent),
        preserve_album_variants=bool(intent.album),
    )


def search_plex(intent: MusicIntent) -> list[dict[str, Any]]:
    return search_music_catalog(intent)


def _search_track_from_album_fallback(intent: MusicIntent, settings: dict[str, Any]) -> list[dict[str, Any]]:
    return _search_track_from_album_fallback_via_client(intent, settings)

def _fetch_xml(endpoint: str, settings: dict[str, Any]) -> str:
    return _fetch_xml_via_client(endpoint, settings)
