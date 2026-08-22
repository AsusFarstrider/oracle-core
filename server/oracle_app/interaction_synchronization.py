from __future__ import annotations

from functools import wraps
from contextlib import contextmanager
from threading import RLock
from typing import Callable, Iterator, ParamSpec, TypeVar


_P = ParamSpec("_P")
_R = TypeVar("_R")


class SynchronizationBoundary:
    """One process-local re-entrant transaction boundary."""

    def __init__(self) -> None:
        self._lock = RLock()

    def synchronized(self, operation: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(operation)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with self._lock:
                return operation(*args, **kwargs)

        return wrapped

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield


INTERACTION_SYNCHRONIZATION = SynchronizationBoundary()
synchronized_interaction = INTERACTION_SYNCHRONIZATION.synchronized
