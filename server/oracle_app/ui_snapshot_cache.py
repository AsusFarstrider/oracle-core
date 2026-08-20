from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .interaction_synchronization import SynchronizationBoundary

logger = logging.getLogger("oracle-brain.ui.snapshot_cache")


@dataclass
class _SnapshotCacheEntry:
    payload: dict[str, object]
    stored_monotonic: float


_CACHE: dict[str, _SnapshotCacheEntry] = {}
_SYNCHRONIZATION = SynchronizationBoundary()


@_SYNCHRONIZATION.synchronized
def get_cached_snapshot(
    cache_key: str,
    *,
    ttl_seconds: float,
    builder: Callable[[], dict[str, object]],
) -> dict[str, object]:
    now = time.monotonic()
    entry = _CACHE.get(cache_key)
    if entry is not None:
        age_seconds = max(0.0, now - entry.stored_monotonic)
        if age_seconds <= ttl_seconds:
            logger.info(
                "ui_snapshot_cache_hit cache_key=%s age_ms=%.1f ttl_ms=%.1f",
                cache_key,
                age_seconds * 1000,
                ttl_seconds * 1000,
            )
            return copy.deepcopy(entry.payload)

    started = time.perf_counter()
    payload = builder()
    elapsed_ms = (time.perf_counter() - started) * 1000
    _CACHE[cache_key] = _SnapshotCacheEntry(payload=copy.deepcopy(payload), stored_monotonic=now)
    logger.info(
        "ui_snapshot_cache_refresh cache_key=%s elapsed_ms=%.1f ttl_ms=%.1f",
        cache_key,
        elapsed_ms,
        ttl_seconds * 1000,
    )
    return copy.deepcopy(payload)


@_SYNCHRONIZATION.synchronized
def invalidate_cached_snapshots(prefix: str | None = None) -> None:
    if prefix is None:
        _CACHE.clear()
        logger.info("ui_snapshot_cache_invalidated prefix=*")
        return

    keys = [key for key in _CACHE if key.startswith(prefix)]
    for key in keys:
        _CACHE.pop(key, None)
    if keys:
        logger.info("ui_snapshot_cache_invalidated prefix=%s count=%d", prefix, len(keys))


@_SYNCHRONIZATION.synchronized
def clear_cached_snapshots() -> None:
    invalidate_cached_snapshots()
