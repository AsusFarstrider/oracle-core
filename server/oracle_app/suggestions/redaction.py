from __future__ import annotations

import re
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

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_-]?key|apikey|password|secret|credential)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\b(authorization)(\s*[:=]\s*)(?:(?:bearer|basic)\s+)?[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_SECRET_QUERY_PARAMETER = re.compile(
    r"(?i)([?&](?:access_token|token|api[_-]?key|apikey|password|secret)=)[^&#\s]+"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
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
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    """Redact credentials embedded in free-form provider errors and logs."""

    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _AUTHORIZATION_VALUE.sub(r"\1\2[REDACTED]", value)
    value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    return _SECRET_QUERY_PARAMETER.sub(r"\1[REDACTED]", value)
