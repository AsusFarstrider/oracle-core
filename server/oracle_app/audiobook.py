from __future__ import annotations

import json
import uuid
from typing import Any
from urllib import error, parse, request

from fastapi import HTTPException

from .audiobook_runtime.client import (
    close_audiobook_session as _close_audiobook_session_via_client,
    fetch_audiobook_item as _fetch_audiobook_item_via_client,
    fetch_audiobook_stream as _fetch_audiobook_stream_via_client,
    fetch_current_audiobook_progress as _fetch_current_audiobook_progress_via_client,
    open_audiobook_playback_session as _open_audiobook_playback_session_via_client,
    request_json as _request_json_via_client,
    request_raw as _request_raw_via_client,
    request_text as _request_text_via_client,
    search_audiobooks as _search_audiobooks_via_client,
    sync_audiobook_session as _sync_audiobook_session_via_client,
)
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
from .config import get_audiobook_settings, get_oracle_base_url


LONGFORM_SUPPORTED_MIME_TYPES = (
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4b",
    "audio/aac",
)



def check_audiobook_health() -> dict[str, Any]:
    settings = get_audiobook_settings()
    if not settings["configured"]:
        return {
            "status": "failed",
            "service": "oracle-brain",
            "audiobookshelf_configured": False,
            "configured_satellites": list(settings["satellites"].keys()),
            "detail": "Audiobookshelf is not configured",
        }
    try:
        payload = _request_json("/ping", method="GET")
        ok = bool(payload.get("success"))
        return {
            "status": "ok" if ok else "failed",
            "service": "oracle-brain",
            "audiobookshelf_configured": True,
            "configured_satellites": list(settings["satellites"].keys()),
            "detail": "Audiobookshelf reachable" if ok else "Audiobookshelf ping failed",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "service": "oracle-brain",
            "audiobookshelf_configured": True,
            "configured_satellites": list(settings["satellites"].keys()),
            "detail": str(exc),
        }


def search_audiobooks(
    query: str,
    narrator_preference: str | None = None,
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    return _search_audiobooks_via_client(query, narrator_preference, user_id=user_id)


def find_audiobook_series_entry(series: str, ordinal: int, *, user_id: str | None = None) -> dict[str, Any] | None:
    candidates = search_audiobooks(series, user_id=user_id)
    return _find_audiobook_series_entry(
        series,
        ordinal,
        candidates=candidates,
    )


def fetch_audiobook_item(library_item_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    return _fetch_audiobook_item_via_client(library_item_id, user_id=user_id)


def fetch_audiobook_cover(library_item_id: str, *, user_id: str | None = None):
    return _request_raw_via_client(
        f"/api/items/{parse.quote(library_item_id)}/cover",
        method="GET",
        user_id=user_id,
    )


def fetch_current_audiobook_progress(*, user_id: str | None = None) -> dict[str, Any] | None:
    return _fetch_current_audiobook_progress_via_client(user_id=user_id)


def open_audiobook_playback_session(library_item_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    return _open_audiobook_playback_session_via_client(
        library_item_id,
        supported_mime_types=list(LONGFORM_SUPPORTED_MIME_TYPES),
        user_id=user_id,
    )


def sync_audiobook_session(
    session_id: str,
    *,
    current_time: float,
    time_listened: float,
    duration: float,
    user_id: str | None = None,
) -> None:
    _sync_audiobook_session_via_client(
        session_id,
        current_time=current_time,
        time_listened=time_listened,
        duration=duration,
        user_id=user_id,
    )


def close_audiobook_session(
    session_id: str,
    *,
    current_time: float,
    time_listened: float,
    duration: float,
    user_id: str | None = None,
) -> None:
    _close_audiobook_session_via_client(
        session_id,
        current_time=current_time,
        time_listened=time_listened,
        duration=duration,
        user_id=user_id,
    )


def build_longform_payload(
    session: dict[str, Any],
    *,
    source: str,
    user_id: str | None = None,
    start_paused: bool = False,
    oracle_base_url: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    session = normalize_audiobook_playback_session(session)
    playback_id = uuid.uuid4().hex
    tracks = session.get("tracks") or []
    if not isinstance(tracks, list) or not tracks:
        raise RuntimeError("Audiobookshelf session did not include playable audio tracks")

    effective_oracle_base_url = oracle_base_url or get_oracle_base_url()
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
                    f"{effective_oracle_base_url}/api/satellite/media/audiobooks/"
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


def fetch_audiobook_stream(
    playback: dict[str, Any],
    track_index: int,
    *,
    range_header: str | None = None,
):
    return _fetch_audiobook_stream_via_client(playback, track_index, range_header=range_header)


def _request_json(path: str, *, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request_json_via_client(path, method=method, payload=payload)


def _request_text(path: str, *, method: str, payload: dict[str, Any] | None = None) -> str:
    return _request_text_via_client(path, method=method, payload=payload)


def _request(path: str, *, method: str, payload: dict[str, Any] | None = None):
    return _request_raw_via_client(path, method=method, payload=payload)
