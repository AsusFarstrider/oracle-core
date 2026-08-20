"""Small shared vocabulary for domain-owned reconstructable caches."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class CacheDiagnostics:
    cache_id: str
    path: str
    exists: bool
    healthy: bool
    entry_count: int
    total_bytes: int
    limit_entries: int
    limit_bytes: int | None
    expired_entries: int = 0
    malformed_entries: int = 0
    legacy_entries: int = 0
    oldest_accessed_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CacheMaintenanceResult:
    cache_id: str
    inspected_entries: int
    removed_expired: int
    removed_malformed: int
    removed_legacy: int
    removed_lru: int
    bytes_reclaimed: int
    diagnostics: CacheDiagnostics

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["diagnostics"] = self.diagnostics.as_dict()
        return payload


class CacheLifecycle(Protocol):
    def cache_diagnostics(self, *, now: float | None = None) -> CacheDiagnostics: ...

    def maintain_cache(self, *, now: float | None = None) -> CacheMaintenanceResult: ...
