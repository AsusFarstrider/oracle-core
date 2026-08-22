from __future__ import annotations

import json
from pathlib import Path

from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan


MATRIX_PATH = Path(__file__).resolve().parent / "fixtures" / "dispatch_reply_characterization.json"


def _entries() -> list[dict[str, object]]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_dispatch_reply_matrix_has_unique_ids_and_all_targets() -> None:
    entries = _entries()
    ids = [entry["id"] for entry in entries]
    targets = {entry["dispatch"]["target"] for entry in entries}

    assert len(ids) == len(set(ids))
    assert targets == {
        "audiobook",
        "calendar",
        "facts",
        "fallback_router",
        "home_assistant",
        "music",
        "network",
        "news",
        "system",
        "weather",
    }


def test_characterized_dispatch_envelopes_round_trip_without_shape_drift() -> None:
    for entry in _entries():
        dispatch_data = entry["dispatch"]
        dispatch = DispatchPlan.model_validate(dispatch_data)
        assert dispatch.model_dump(mode="json") == dispatch_data, entry["id"]


def test_characterized_replies_are_byte_exact() -> None:
    for entry in _entries():
        dispatch = DispatchPlan.model_validate(entry["dispatch"])
        assert build_reply_text(dispatch) == entry["expected_reply"], entry["id"]


def test_matrix_freezes_success_failure_pending_nested_and_silence_paths() -> None:
    entries = _entries()
    statuses = {entry["dispatch"]["status"] for entry in entries}
    result_keys = {
        key
        for entry in entries
        for key in entry["dispatch"]["result"].keys()
    }

    assert {"executed", "failed", "pending_confirmation", "pending_clarification"} <= statuses
    assert {"action", "error", "prompt", "confirmed_dispatch"} <= result_keys
    assert any(entry["expected_reply"] == "" for entry in entries)
