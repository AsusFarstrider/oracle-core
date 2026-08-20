from __future__ import annotations

import json
from typing import Any

from oracle_app.inference import InferenceClient, legacy_inference_client

from .parsing import MusicIntent, optional_list, optional_str


OLLAMA_CAPABILITY_MODES: set[str] = {"answer", "home_assistant", "calendar", "music", "news", "audiobook"}


def parse_ollama_decision(raw_text: str) -> dict[str, str]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end >= start:
        cleaned = cleaned[start : end + 1]
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
    return {"mode": mode, "reply": reply, "command": command, "reason": reason}


def resolve_with_ollama(text: str, *, inference: InferenceClient | None = None) -> MusicIntent | None:
    system = (
        "You extract structured music intents for Oracle. "
        "Return only JSON with keys: intent, media_type, title, artist, album, playlist, genre, qualifiers, mode. "
        "Use null for missing scalar values and [] for qualifiers. "
        "Allowed intents: play. "
        "Allowed media_type: track, album, artist, playlist. "
        "Do not invent media that was not requested."
    )
    try:
        body = _inference(inference).generate(text, system=system, format="json")
    except Exception:
        return None

    raw = str(body.get("response", "")).strip()
    if not raw:
        return None
    try:
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return None

    if str(data.get("intent", "")).strip() != "play":
        return None

    media_type = str(data.get("media_type", "")).strip() or None
    if media_type not in {"track", "album", "artist", "playlist"}:
        return None

    return MusicIntent(
        intent="play",
        media_type=media_type,
        title=optional_str(data.get("title")),
        artist=optional_str(data.get("artist")),
        album=optional_str(data.get("album")),
        playlist=optional_str(data.get("playlist")),
        genre=optional_str(data.get("genre")),
        qualifiers=optional_list(data.get("qualifiers")),
        mode=optional_str(data.get("mode")) or "replace",
        original_text=" ".join(text.strip().lower().split()),
    )


def choose_music_match_with_ollama(
    intent: MusicIntent,
    candidates: list[dict[str, Any]],
    *,
    inference: InferenceClient | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    system = (
        "You choose the best music playback match for Oracle. "
        "Return only JSON with keys: choice_index and reason. "
        "choice_index must be an integer index into the provided candidates array, or -1 if none are good enough. "
        "Prefer exact artist, title, album, and playlist matches. "
        "Avoid picking duplicate-looking reissues unless the user clearly asked for them."
    )
    prompt_candidates = []
    for index, item in enumerate(candidates):
        prompt_candidates.append(
            {
                "index": index,
                "type": item.get("type"),
                "title": item.get("title"),
                "artist": item.get("artist"),
                "album": item.get("album"),
            }
        )

    prompt = json.dumps(
        {
            "request": intent.to_payload(),
            "candidates": prompt_candidates,
        }
    )
    try:
        body = _inference(inference).generate(prompt, system=system, format="json")
    except Exception:
        return None

    raw = str(body.get("response", "")).strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        choice_index = int(parsed.get("choice_index"))
    except (TypeError, ValueError):
        return None
    if not 0 <= choice_index < len(candidates):
        return None
    return candidates[choice_index]


def choose_best_guess_with_ollama(
    request_text: str,
    candidates: list[dict[str, Any]],
    *,
    inference: InferenceClient | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    system = (
        "You choose the single best fallback media guess for Oracle after deterministic matching was too weak. "
        "Return only JSON with keys: choice_index and reason. "
        "choice_index must be an integer index into the provided candidates array, or -1 if nothing is plausible enough. "
        "Prefer candidates that closely match the user's title words and media wording. "
        "Do not choose a weak guess unless it is genuinely plausible."
    )
    prompt_candidates = []
    for index, item in enumerate(candidates):
        prompt_candidates.append(
            {
                "index": index,
                "route_target": item.get("route_target"),
                "media_type": item.get("media_type"),
                "title": item.get("title"),
                "artist": item.get("artist"),
                "album": item.get("album"),
                "author": item.get("author"),
                "score": item.get("score"),
            }
        )

    prompt = json.dumps(
        {
            "request_text": request_text,
            "candidates": prompt_candidates,
        }
    )
    try:
        body = _inference(inference).generate(prompt, system=system, format="json")
    except Exception:
        return None

    raw = str(body.get("response", "")).strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        choice_index = int(parsed.get("choice_index"))
    except (TypeError, ValueError):
        return None
    if not 0 <= choice_index < len(candidates):
        return None
    return candidates[choice_index]


def _inference(inference: InferenceClient | None) -> InferenceClient:
    if inference is not None:
        return inference
    return legacy_inference_client()
