from __future__ import annotations

import json
import socket
from typing import Any
from urllib import request


def call_generate(
    *,
    base_url: str,
    model: str,
    prompt: str,
    timeout_seconds: int,
    keep_alive: int | str,
    options: dict[str, Any],
    system: str | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    endpoint = f"{base_url}/api/generate"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
        "options": options,
    }
    if system:
        payload["system"] = system
    if format:
        payload["format"] = format

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    attempt_timeouts = (timeout_seconds, timeout_seconds * 2)
    last_timeout: TimeoutError | socket.timeout | None = None
    for attempt_timeout in attempt_timeouts:
        try:
            with request.urlopen(req, timeout=attempt_timeout) as response:
                raw_body = response.read().decode("utf-8")
                return json.loads(raw_body)
        except (TimeoutError, socket.timeout) as exc:
            last_timeout = exc

    if last_timeout is not None:
        raise last_timeout
    raise TimeoutError("Model request timed out")


def warm_model(
    *,
    base_url: str,
    model: str,
    timeout_seconds: int | float,
    keep_alive: int | str,
) -> None:
    endpoint = f"{base_url}/api/generate"
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_predict": 0},
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        response.read()
