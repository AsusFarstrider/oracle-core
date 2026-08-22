from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from oracle_app import audiobook_state


def setup_function() -> None:
    audiobook_state.clear_all_active_audiobook_playbacks()
    audiobook_state.clear_all_pending_audiobook_syncs()


def test_register_playback_replaces_previous_source_mapping() -> None:
    audiobook_state.register_active_audiobook_playback(
        "playback-1",
        {"playback_id": "playback-1", "source": "satellite-alpha"},
    )
    audiobook_state.register_active_audiobook_playback(
        "playback-2",
        {"playback_id": "playback-2", "source": "satellite-alpha"},
    )

    assert audiobook_state.get_active_audiobook_playback("playback-1") is None
    assert audiobook_state.get_active_audiobook_playback_for_source("satellite-alpha") == {
        "playback_id": "playback-2",
        "source": "satellite-alpha",
    }


def test_clear_playback_removes_source_mapping() -> None:
    audiobook_state.register_active_audiobook_playback(
        "playback-1",
        {"playback_id": "playback-1", "source": "satellite-alpha"},
    )

    audiobook_state.clear_active_audiobook_playback("playback-1")

    assert audiobook_state.get_active_audiobook_playback("playback-1") is None
    assert audiobook_state.get_active_audiobook_playback_for_source("satellite-alpha") is None


def test_playback_reads_return_deep_copies() -> None:
    audiobook_state.register_active_audiobook_playback(
        "playback-1",
        {
            "playback_id": "playback-1",
            "source": "satellite-alpha",
            "tracks": [{"content_url": "/audio/book.mp3"}],
        },
    )

    payload = audiobook_state.get_active_audiobook_playback("playback-1")
    assert payload is not None
    payload["tracks"][0]["content_url"] = "/mutated"

    fresh = audiobook_state.get_active_audiobook_playback("playback-1")
    assert fresh is not None
    assert fresh["tracks"][0]["content_url"] == "/audio/book.mp3"


def test_concurrent_source_registration_leaves_one_consistent_playback() -> None:
    def register(index: int) -> None:
        audiobook_state.register_active_audiobook_playback(
            f"playback-{index}",
            {"playback_id": f"playback-{index}", "source": "satellite-alpha"},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(register, range(100)))

    active = audiobook_state.get_active_audiobook_playback_for_source("satellite-alpha")
    assert active is not None
    active_id = active["playback_id"]
    assert audiobook_state.get_active_audiobook_playback(active_id) == active
