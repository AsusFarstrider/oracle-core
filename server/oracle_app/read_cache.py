from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from .informational_evidence import InformationFailureKind


T = TypeVar("T")


@dataclass(frozen=True)
class CachedRead(Generic[T]):
    value: T
    freshness: str
    age_seconds: float
    stale_reason: str | None = None
    refresh_status: str = "not_attempted"
    failure_kind: str | None = None


@dataclass
class _Entry(Generic[T]):
    value: T
    stored_at: float


@dataclass
class _InFlightRead(Generic[T]):
    event: threading.Event
    value: T | None = None
    stored_at: float | None = None
    error: Exception | None = None


def classify_read_failure(error: Exception) -> InformationFailureKind:
    """Classify cache-loader failure without turning programming defects stale."""

    name = type(error).__name__.casefold()
    if isinstance(error, TimeoutError) or "timeout" in name:
        return InformationFailureKind.TIMEOUT
    if "configuration" in name or "config" in name:
        return InformationFailureKind.CONFIGURATION
    if "contract" in name or "schema" in name or "validation" in name:
        return InformationFailureKind.CONTRACT
    if isinstance(error, (AssertionError, KeyError, TypeError, ValueError)):
        return InformationFailureKind.PROGRAMMING
    if isinstance(error, (ConnectionError, OSError, RuntimeError)):
        return InformationFailureKind.PROVIDER
    return InformationFailureKind.UNKNOWN


class BoundedReadCache(Generic[T]):
    """Small in-process read cache with bounded stale-on-error fallback."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry[T]] = {}
        self._inflight: dict[str, _InFlightRead[T]] = {}
        self._generation = 0
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
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("cache key is required")
        if ttl_seconds < 0 or stale_max_seconds < 0:
            raise ValueError("cache freshness bounds must not be negative")
        now = self._clock()
        with self._lock:
            entry = self._entries.get(normalized_key)
            age = max(0.0, now - entry.stored_at) if entry is not None else 0.0
            if entry is not None and not force_refresh and age <= ttl_seconds:
                return CachedRead(copy.deepcopy(entry.value), "fresh", age)

            flight = self._inflight.get(normalized_key)
            if flight is None:
                flight = _InFlightRead(event=threading.Event())
                self._inflight[normalized_key] = flight
                leader = True
                generation = self._generation
            else:
                leader = False
                generation = self._generation

        if not leader:
            flight.event.wait()
            if flight.error is not None:
                return self._stale_or_raise(
                    normalized_key,
                    error=flight.error,
                    stale_max_seconds=stale_max_seconds,
                    allow_stale=allow_stale,
                )
            return CachedRead(
                copy.deepcopy(flight.value),  # type: ignore[arg-type]
                "fresh",
                max(0.0, self._clock() - float(flight.stored_at or self._clock())),
                refresh_status="succeeded",
            )

        try:
            value = loader()
        except Exception as exc:
            with self._lock:
                flight.error = exc
                self._inflight.pop(normalized_key, None)
                flight.event.set()
            return self._stale_or_raise(
                normalized_key,
                error=exc,
                stale_max_seconds=stale_max_seconds,
                allow_stale=allow_stale,
            )

        stored_at = self._clock()
        with self._lock:
            if generation == self._generation:
                self._entries[normalized_key] = _Entry(copy.deepcopy(value), stored_at)
            flight.value = copy.deepcopy(value)
            flight.stored_at = stored_at
            self._inflight.pop(normalized_key, None)
            flight.event.set()
        return CachedRead(copy.deepcopy(value), "fresh", 0.0, refresh_status="succeeded")

    def _stale_or_raise(
        self,
        key: str,
        *,
        error: Exception,
        stale_max_seconds: float,
        allow_stale: bool,
    ) -> CachedRead[T]:
        failure_kind = classify_read_failure(error)
        may_serve_stale = failure_kind in {
            InformationFailureKind.PROVIDER,
            InformationFailureKind.TIMEOUT,
        }
        with self._lock:
            entry = self._entries.get(key)
            age = max(0.0, self._clock() - entry.stored_at) if entry is not None else 0.0
            if entry is not None and allow_stale and may_serve_stale and age <= stale_max_seconds:
                stale_reason = (
                    "provider_refresh_timed_out"
                    if failure_kind == InformationFailureKind.TIMEOUT
                    else "provider_refresh_failed"
                )
                return CachedRead(
                    copy.deepcopy(entry.value),
                    "stale",
                    age,
                    stale_reason,
                    refresh_status="failed",
                    failure_kind=failure_kind.value,
                )
        raise error

    def invalidate(self, prefix: str | None = None) -> None:
        with self._lock:
            self._generation += 1
            if prefix is None:
                self._entries.clear()
                return
            for key in [candidate for candidate in self._entries if candidate.startswith(prefix)]:
                self._entries.pop(key, None)
