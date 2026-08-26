"""Reconstructible runtime cache access; configuration lives in composition."""

from __future__ import annotations

import json
from typing import Any

from .constants import CACHE_PATH


def load_home_assistant_cache() -> dict[str, Any]:
    """Load reconstructible Home Assistant cache data, not configuration authority."""

    if not CACHE_PATH.exists():
        return {}
    with CACHE_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}
