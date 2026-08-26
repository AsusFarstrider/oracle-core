from __future__ import annotations

from typing import Any

from .music_runtime.canonical import CanonicalMusicExecution
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
) -> dict[str, Any]:
    configured = music_execution is not None
    return {
        "status": "ok" if configured else "disabled",
        "service": "oracle-brain",
        "plex_configured": configured,
        "configured_satellites": [] if music_execution is None else sorted(music_execution.settings.playback_targets),
        "detail": "Music routing configured" if configured else "Music is disabled in canonical configuration",
    }
