from __future__ import annotations

from typing import Any, Dict


def extract_spoken_reply(payload: Dict[str, Any]) -> str:
    reply_text = str(payload.get("reply_text", "")).strip()
    if reply_text:
        return reply_text

    dispatch = payload.get("dispatch", {})
    status = dispatch.get("status")
    result = dispatch.get("result") or {}

    if status == "pending_confirmation":
        return str(result.get("prompt", "Please confirm before I proceed.")).strip()

    if status == "pending_clarification":
        return str(result.get("prompt", "I found multiple matches. Which one did you want?")).strip()

    if dispatch.get("target") == "system" and result.get("action") == "ignore":
        return ""

    if status == "failed":
        return "I couldn't complete that request."

    return "Done."
