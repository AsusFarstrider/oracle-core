from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


_CORRELATION_ID: ContextVar[str | None] = ContextVar("oracle_memory_correlation_id", default=None)
_VALID_CORRELATION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def new_correlation_id() -> str:
    return f"corr_{uuid.uuid4().hex}"


def normalize_correlation_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if not _VALID_CORRELATION_RE.fullmatch(normalized):
        return None
    return normalized


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


def set_correlation_id(correlation_id: str | None) -> Token[str | None]:
    return _CORRELATION_ID.set(normalize_correlation_id(correlation_id))


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    resolved = normalize_correlation_id(correlation_id) or new_correlation_id()
    token = set_correlation_id(resolved)
    try:
        yield resolved
    finally:
        _CORRELATION_ID.reset(token)
