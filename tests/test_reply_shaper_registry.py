from __future__ import annotations

from pathlib import Path

from oracle_app.replies import REPLY_SHAPERS, build_reply_text
from oracle_app.schemas import DispatchPlan


def test_reply_registry_has_one_pure_shaper_for_every_target() -> None:
    assert set(REPLY_SHAPERS) == {
        "audiobook", "calendar", "facts", "fallback_router", "home_assistant",
        "music", "network", "news", "system", "weather",
    }


def test_registry_preserves_shared_pending_reply_exactly() -> None:
    dispatch = DispatchPlan(
        target="music",
        hook="music.execute",
        payload={"text": "play heroes"},
        status="pending_clarification",
        result={"prompt": "Which version of Heroes did you mean?"},
    )
    assert build_reply_text(dispatch) == "Which version of Heroes did you mean?"


def test_pc_push_to_talk_has_no_domain_reply_interpreter() -> None:
    source = (Path(__file__).resolve().parents[1] / "satellite" / "pc_push_to_talk.py").read_text()
    method = source.split("    def _extract_spoken_reply", 1)[1].split("    def _request_tts", 1)[0]
    assert "dispatch" not in method
    assert "reply_text" in method
