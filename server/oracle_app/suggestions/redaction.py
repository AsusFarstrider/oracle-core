from __future__ import annotations

from typing import Any


_SECRET_KEY_PARTS = (
    "token",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "credential",
)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SECRET_KEY_PARTS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value
