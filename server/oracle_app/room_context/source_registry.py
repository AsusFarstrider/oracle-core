from __future__ import annotations

from typing import Any

from oracle_app.config import get_source_registry


def get_source_entry(source: str | None) -> dict[str, Any] | None:
    source_key = str(source or "").strip()
    if not source_key:
        return None
    entry = get_source_registry().get(source_key)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def is_fixed_source(source: str | None) -> bool:
    entry = get_source_entry(source)
    return bool((entry or {}).get("fixed"))
