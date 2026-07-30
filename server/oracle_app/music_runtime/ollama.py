from __future__ import annotations

import json
from urllib import error, request

from oracle_app.config import get_ollama_request_settings, get_ollama_settings

from .parsing import MusicIntent, optional_list, optional_str


def resolve_with_ollama(text: str) -> MusicIntent | None:
    base_url, model = get_ollama_settings()
    settings = get_ollama_request_settings()
    endpoint = f"{base_url}/api/generate"
    system = (
        "You extract structured music intents for Oracle. "
        "Return only JSON with keys: intent, media_type, title, artist, album, playlist, genre, qualifiers, mode. "
        "Use null for missing scalar values and [] for qualifiers. "
        "Allowed intents: play. "
        "Allowed media_type: track, album, artist, playlist. "
        "Do not invent media that was not requested."
    )
    payload = {
        "model": model,
        "prompt": text,
        "system": system,
        "format": "json",
        "stream": False,
        "keep_alive": settings["keep_alive"],
        "options": settings["options"],
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=settings["timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
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
) -> dict[str, Any] | None:
    if not candidates:
        return None

    base_url, model = get_ollama_settings()
    settings = get_ollama_request_settings()
    endpoint = f"{base_url}/api/generate"
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
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "format": "json",
        "stream": False,
        "keep_alive": settings["keep_alive"],
        "options": settings["options"],
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=settings["timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
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
) -> dict[str, Any] | None:
    if not candidates:
        return None

    base_url, model = get_ollama_settings()
    settings = get_ollama_request_settings()
    endpoint = f"{base_url}/api/generate"
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
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "format": "json",
        "stream": False,
        "keep_alive": settings["keep_alive"],
        "options": settings["options"],
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=settings["timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
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
