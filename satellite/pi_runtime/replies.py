from __future__ import annotations

from typing import Any, Dict


def extract_spoken_reply(payload: Dict[str, Any]) -> str:
    status = str(payload.get("status") or "").strip()
    if status not in {
        "executed", "pending_confirmation", "pending_clarification", "failed", "ignored"
    }:
        raise RuntimeError("Oracle returned an unknown conversation status")
    effects = payload.get("effects")
    if not isinstance(effects, dict) or set(effects) != {
        "follow_up", "satellite_playback", "deferred_satellite_playback", "ui_presentation"
    }:
        raise RuntimeError("Oracle returned an invalid conversation effects contract")
    reply_text = str(payload.get("reply_text") or "").strip()
    if status == "ignored":
        return reply_text
    if not reply_text:
        raise RuntimeError("Oracle returned a conversation result without reply text")
    return reply_text
