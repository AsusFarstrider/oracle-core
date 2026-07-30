from __future__ import annotations

import json
import logging
from typing import Any

from oracle_app.config import get_ollama_request_settings, get_ollama_settings
from oracle_app.llm_bridge import call_generate, warm_model

logger = logging.getLogger("oracle-brain.ollama")


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def call_ollama_generate(
    prompt: str,
    *,
    system: str | None = None,
) -> dict[str, Any]:
    base_url, model = get_ollama_settings()
    request_settings = get_ollama_request_settings()
    return call_generate(
        base_url=base_url,
        model=model,
        prompt=prompt,
        timeout_seconds=int(request_settings["timeout_seconds"]),
        keep_alive=request_settings["keep_alive"],
        options=dict(request_settings["options"]),
        system=system,
        format="json",
    )


def warm_ollama_model() -> None:
    base_url, model = get_ollama_settings()
    request_settings = get_ollama_request_settings()
    warm_model(
        base_url=base_url,
        model=model,
        timeout_seconds=int(request_settings["timeout_seconds"]),
        keep_alive=request_settings["keep_alive"],
    )


OLLAMA_CAPABILITY_MODES: set[str] = {"answer", "home_assistant", "calendar", "music", "news", "audiobook"}


def parse_ollama_decision(raw_text: str) -> dict[str, str]:
    cleaned = raw_text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()

    cleaned = _extract_json_object(cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "mode": "answer",
            "reply": raw_text.strip(),
            "command": "",
            "reason": "Model did not return valid JSON; treated as a non-executable answer",
        }

    mode = parsed.get("mode")
    if mode not in OLLAMA_CAPABILITY_MODES:
        return {
            "mode": "answer",
            "reply": raw_text.strip(),
            "command": "",
            "reason": "Model returned an invalid mode; treated as a non-executable answer",
        }

    reply = str(parsed.get("reply", "")).strip()
    command = str(parsed.get("command", "")).strip()
    reason = str(parsed.get("reason", "")).strip()

    if mode != "answer" and not command:
        return {
            "mode": "answer",
            "reply": reply or "I need a clearer request before I can act on it.",
            "command": "",
            "reason": reason or "Model selected an executable mode without a command",
        }

    return {
        "mode": mode,
        "reply": reply,
        "command": command,
        "reason": reason,
    }
