from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class CachedRead(Generic[T]):
    value: T
    freshness: str
    age_seconds: float
    stale_reason: str | None = None


@dataclass
class _Entry(Generic[T]):
    value: T
    stored_at: float


class BoundedReadCache(Generic[T]):
    """Small in-process read cache with bounded stale-on-error fallback."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = threading.Lock()

    def read(
        self,
        key: str,
        *,
        ttl_seconds: float,
        stale_max_seconds: float,
        loader: Callable[[], T],
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> CachedRead[T]:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            age = max(0.0, now - entry.stored_at) if entry is not None else 0.0
            if entry is not None and not force_refresh and age <= ttl_seconds:
                return CachedRead(copy.deepcopy(entry.value), "fresh", age)

        try:
            value = loader()
        except Exception:
            with self._lock:
                entry = self._entries.get(key)
                age = max(0.0, self._clock() - entry.stored_at) if entry is not None else 0.0
                if entry is not None and allow_stale and age <= stale_max_seconds:
                    return CachedRead(
                        copy.deepcopy(entry.value),
                        "stale",
                        age,
                        "provider_refresh_failed",
                    )
            raise

        stored_at = self._clock()
        with self._lock:
            self._entries[key] = _Entry(copy.deepcopy(value), stored_at)
        return CachedRead(copy.deepcopy(value), "fresh", 0.0)

    def invalidate(self, prefix: str | None = None) -> None:
        with self._lock:
            if prefix is None:
                self._entries.clear()
                return
            for key in [candidate for candidate in self._entries if candidate.startswith(prefix)]:
                self._entries.pop(key, None)

