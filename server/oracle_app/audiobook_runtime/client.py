from __future__ import annotations

from typing import Any

from oracle_app.config import get_audiobook_settings
from oracle_app.provider_bridges import get_audiobook_bridge


def _bridge():
    return get_audiobook_bridge(get_audiobook_settings())


def search_audiobooks(
    query: str,
    narrator_preference: str | None = None,
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    return _bridge().search_titles(query, narrator_preference, user_id=user_id)


def fetch_audiobook_item(library_item_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    return _bridge().fetch_item(library_item_id, user_id=user_id)


def fetch_current_audiobook_progress(*, user_id: str | None = None) -> dict[str, Any] | None:
    return _bridge().fetch_current_progress(user_id=user_id)


def open_audiobook_playback_session(
    library_item_id: str,
    *,
    supported_mime_types: list[str],
    user_id: str | None = None,
) -> dict[str, Any]:
    return _bridge().open_playback_session(
        library_item_id,
        supported_mime_types=supported_mime_types,
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
    _bridge().sync_session(
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
    _bridge().close_session(
        session_id,
        current_time=current_time,
        time_listened=time_listened,
        duration=duration,
        user_id=user_id,
    )


def fetch_audiobook_stream(
    playback: dict[str, Any],
    track_index: int,
    *,
    range_header: str | None = None,
):
    return _bridge().fetch_stream(playback, track_index, range_header=range_header)


def request_json(
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return _bridge().request_json(path, method=method, payload=payload, user_id=user_id)


def request_text(
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> str:
    return _bridge().request_text(path, method=method, payload=payload, user_id=user_id)


def request_raw(
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
):
    return _bridge().request_raw(path, method=method, payload=payload, user_id=user_id)
