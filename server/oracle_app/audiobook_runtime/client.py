"""Canonical-only audiobook helpers with an explicitly supplied bridge."""

from __future__ import annotations

from typing import Any

from oracle_app.provider_bridges.audiobookshelf_audiobook import AudiobookshelfAudiobookBridge


def fetch_audiobook_stream(
    bridge: AudiobookshelfAudiobookBridge,
    playback: dict[str, Any],
    track_index: int,
    *,
    range_header: str | None = None,
):
    return bridge.fetch_stream(playback, track_index, range_header=range_header)
