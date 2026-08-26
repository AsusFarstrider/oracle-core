from __future__ import annotations

import uuid
from typing import Any

from .provider_bridges.audiobookshelf_audiobook import normalize_audiobook_playback_session
from .audiobook_runtime.matching import (
    build_search_queries as _build_search_queries,
    choose_audiobook_match,
    find_audiobook_series_entry as _find_audiobook_series_entry,
    score_audiobook_candidates,
)
from .audiobook_runtime.parsing import (
    AudiobookIntent,
    is_audiobook_request,
    parse_audiobook_intent,
    parse_bare_audiobook_sleep_timer_intent,
)


LONGFORM_SUPPORTED_MIME_TYPES = (
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4b",
    "audio/aac",
)



def build_longform_payload(
    session: dict[str, Any],
    *,
    source: str,
    user_id: str | None = None,
    start_paused: bool = False,
    oracle_base_url: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    session = normalize_audiobook_playback_session(session)
    playback_id = uuid.uuid4().hex
    tracks = session.get("tracks") or []
    if not isinstance(tracks, list) or not tracks:
        raise RuntimeError("Audiobookshelf session did not include playable audio tracks")

    normalized_tracks: list[dict[str, Any]] = []
    upstream_tracks: list[dict[str, Any]] = []
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        relative_url = str(track.get("content_url", "")).strip()
        if not relative_url:
            continue
        normalized_tracks.append(
            {
                "url": (
                    f"{oracle_base_url}/api/satellite/media/audiobooks/"
                    f"{playback_id}/tracks/{index}"
                ),
                "mime_type": str(track.get("mime_type", "")).strip(),
                "duration_seconds": float(track.get("duration_seconds") or 0),
                "start_offset_seconds": float(track.get("start_offset_seconds") or 0),
                "title": str(track.get("title", "")).strip(),
            }
        )
        upstream_tracks.append(
            {
                "content_url": relative_url,
                "mime_type": str(track.get("mime_type", "")).strip(),
            }
        )

    if not normalized_tracks:
        raise RuntimeError("Audiobookshelf session returned no usable audio track URLs")

    chapters = session.get("chapters") or []
    chapter_payload = []
    if isinstance(chapters, list):
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_payload.append(
                {
                    "title": str(chapter.get("title", "")).strip(),
                    "start_seconds": float(chapter.get("start_seconds") or 0),
                    "end_seconds": float(chapter.get("end_seconds") or 0),
                }
            )

    longform_payload = {
        "playback_id": playback_id,
        "session_id": str(session.get("provider_session_id", "")).strip(),
        "title": str(session.get("title", "")).strip(),
        "author": str(session.get("author", "")).strip(),
        "duration_seconds": float(session.get("duration_seconds") or 0),
        "start_position_seconds": float(session.get("current_time_seconds") or 0),
        "start_paused": bool(start_paused),
        "tracks": normalized_tracks,
        "chapters": chapter_payload,
    }
    state_payload = {
        "playback_id": playback_id,
        "provider_session_id": str(session.get("provider_session_id", "")).strip(),
        "library_item_id": str(session.get("library_item_id", "")).strip(),
        "source": source,
        "user_id": str(user_id or "").strip() or None,
        "duration_seconds": float(session.get("duration_seconds") or 0),
        "start_position_seconds": longform_payload["start_position_seconds"],
        "title": longform_payload["title"],
        "author": longform_payload["author"],
        "tracks": upstream_tracks,
    }
    return playback_id, longform_payload, state_payload
