from __future__ import annotations

import json
from types import SimpleNamespace

from oracle_app.ui_audio import build_ui_audio_snapshot, build_ui_audio_status_snapshot


class _CountingAudiobookExecution:
    def __init__(self) -> None:
        self.authority_calls = 0
        self.progress_calls = 0
        self.settings = SimpleNamespace(
            playback_targets={"office"},
            playback_target=lambda source: object() if source == "office" else None,
        )

    def fetch_playback_authority(self, source: str) -> dict[str, object]:
        self.authority_calls += 1
        owner = {
            "session_id": "session-1",
            "backend_type": "oracle_audiobook",
            "media_kind": "audiobook",
            "state": "playing",
            "title": "A Useful Story",
            "artist_or_author": "A. Reader",
            "position_seconds": 125.0,
            "duration_seconds": 600.0,
            "updated_at": "2026-08-25T12:00:00Z",
            "resumable": True,
        }
        return {"playback_active": True, "active_sessions": [owner], "output_owner": owner}

    def fetch_current_progress(self, *, user_id: str | None = None) -> dict[str, object]:
        del user_id
        self.progress_calls += 1
        return {
            "library_item_id": "book-1",
            "title": "A Useful Story",
            "author": "A. Reader",
            "current_time_seconds": 120.0,
            "duration_seconds": 600.0,
        }


def test_compact_status_uses_runtime_authority_without_provider_progress() -> None:
    execution = _CountingAudiobookExecution()

    payload = build_ui_audio_status_snapshot(
        "office",
        audiobook_execution=execution,
        household_settings=None,
    )

    owner = payload["playback"]["output_owner"]
    assert execution.authority_calls == 1
    assert execution.progress_calls == 0
    assert owner["position_seconds"] == 125.0
    assert owner["duration_seconds"] == 600.0
    assert owner["updated_at"] == "2026-08-25T12:00:00Z"
    assert payload["progress_observation"] == {
        "basis": "runtime_playback_authority",
        "estimated": False,
    }
    assert payload["refresh_after_seconds"] == 5


def test_full_audio_snapshot_retains_provider_progress_contract() -> None:
    execution = _CountingAudiobookExecution()

    payload = build_ui_audio_snapshot(
        "office",
        audiobook_execution=execution,
        household_settings=None,
    )

    assert execution.authority_calls == 1
    assert execution.progress_calls == 1
    assert payload["current_audiobook"]["current_time_seconds"] == 120.0
    assert "users" in payload
    assert "capabilities" in payload


def test_compact_status_is_materially_smaller_than_full_snapshot() -> None:
    execution = _CountingAudiobookExecution()
    compact = build_ui_audio_status_snapshot(
        "office",
        audiobook_execution=execution,
        household_settings=None,
    )
    full = build_ui_audio_snapshot(
        "office",
        audiobook_execution=execution,
        household_settings=None,
    )

    compact_size = len(json.dumps(compact, separators=(",", ":")).encode())
    full_size = len(json.dumps(full, separators=(",", ":")).encode())
    assert compact_size < full_size * 0.7
