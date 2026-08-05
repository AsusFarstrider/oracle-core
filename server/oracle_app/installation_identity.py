"""Deterministic installed-component names derived from semantic identities."""

from __future__ import annotations

import re


ENVIRONMENT_FORMAT = "oracle-python-environment-v1"
ENVIRONMENT_PREFIX = f"{ENVIRONMENT_FORMAT}:sha256:"
_ENVIRONMENT_IDENTITY = re.compile(
    rf"^{re.escape(ENVIRONMENT_PREFIX)}(?P<digest>[0-9a-f]{{64}})$"
)


def environment_directory_name(environment_identity: str) -> str:
    """Return the safe direct-child name for one complete environment identity."""

    if not isinstance(environment_identity, str):
        raise ValueError("Python environment identity is invalid")
    match = _ENVIRONMENT_IDENTITY.fullmatch(environment_identity)
    if match is None:
        raise ValueError("Python environment identity is invalid")
    return f"environment-{match.group('digest')}"
