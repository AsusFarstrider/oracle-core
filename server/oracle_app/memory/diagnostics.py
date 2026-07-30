from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .events import EventQuery, query_events
from .provider_status import ProviderStatusQuery, query_provider_status_snapshots
from .satellite_activity import SatelliteStatusQuery, query_satellite_status_snapshots
from .sources import list_sources
from .store import DB_PATH


DEFAULT_WINDOW_HOURS = 24
DEFAULT_STALE_PROVIDER_HOURS = 24
DEFAULT_STALE_SATELLITE_MINUTES = 15


@dataclass(frozen=True)
class DiagnosticsSummaryQuery:
    observed_after: str | None = None
    observed_before: str | None = None
    event_limit: int = 100
    provider_limit: int = 100
    source_limit: int = 100
    satellite_limit: int = 100
    event_type: str | None = None
    severity: str | None = None
    status: str | None = None
    domain: str | None = None
    provider: str | None = None
    source_type: str | None = None
    satellite_source_id: str | None = None
    satellite_status: str | None = None


def build_memory_diagnostics_summary(
    query: DiagnosticsSummaryQuery | None = None,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_query = query or DiagnosticsSummaryQuery()
    resolved_now = _ensure_aware_utc(now or datetime.now(timezone.utc))
    observed_after = resolved_query.observed_after or (resolved_now - timedelta(hours=DEFAULT_WINDOW_HOURS)).isoformat()
    observed_before = resolved_query.observed_before
    path = db_path or DB_PATH

    events = query_events(
        EventQuery(
            event_type=resolved_query.event_type,
            severity=resolved_query.severity,
            status=resolved_query.status,
            domain=resolved_query.domain,
            observed_after=observed_after,
            observed_before=observed_before,
            limit=resolved_query.event_limit,
        ),
        db_path=path,
    )
    provider_snapshots = query_provider_status_snapshots(
        ProviderStatusQuery(
            provider=resolved_query.provider,
            domain=resolved_query.domain,
            status=resolved_query.status,
            limit=resolved_query.provider_limit,
        ),
        db_path=path,
    )
    source_rows = _filtered_sources(
        list_sources(db_path=path),
        source_type=resolved_query.source_type,
        status=resolved_query.status,
        limit=resolved_query.source_limit,
    )
    satellite_snapshots = _satellite_snapshots_with_stale_state(
        query_satellite_status_snapshots(
            SatelliteStatusQuery(
                source_id=resolved_query.satellite_source_id,
                status=resolved_query.satellite_status,
                limit=resolved_query.satellite_limit,
            ),
            db_path=path,
        ),
        resolved_now,
    )

    return {
        "generated_at": resolved_now.isoformat(),
        "window": {
            "observed_after": observed_after,
            "observed_before": observed_before,
        },
        "events": {
            "total": len(events),
            "limit": _clamp_limit(resolved_query.event_limit),
            "by_type": _count_by(events, "event_type"),
            "by_category": _count_by(events, "category"),
            "by_severity": _count_by(events, "severity"),
            "by_domain": _count_by(events, "domain"),
            "by_status": _count_by(events, "status"),
            "recent": events,
        },
        "providers": {
            "total": len(provider_snapshots),
            "limit": _clamp_limit(resolved_query.provider_limit),
            "by_status": _count_by(provider_snapshots, "status"),
            "by_domain": _count_by(provider_snapshots, "domain"),
            "by_provider": _count_by(provider_snapshots, "provider"),
            "stale_count": _count_stale_provider_snapshots(provider_snapshots, resolved_now),
            "stale_provider_count_threshold_hours": DEFAULT_STALE_PROVIDER_HOURS,
            "latest": provider_snapshots,
        },
        "sources": {
            "total": len(source_rows),
            "limit": _clamp_limit(resolved_query.source_limit),
            "by_type": _count_by(source_rows, "source_type"),
            "by_status": _count_by(source_rows, "status"),
            "items": source_rows,
        },
        "satellites": {
            "total": len(satellite_snapshots),
            "limit": _clamp_limit(resolved_query.satellite_limit),
            "by_status": _count_by(satellite_snapshots, "status"),
            "stale_count": _count_stale_satellite_snapshots(satellite_snapshots),
            "stale_threshold_minutes": DEFAULT_STALE_SATELLITE_MINUTES,
            "latest": satellite_snapshots,
        },
    }


def _filtered_sources(
    source_rows: list[dict[str, Any]],
    *,
    source_type: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clean_source_type = _clean_filter(source_type)
    clean_status = _clean_filter(status)
    if clean_status in {"available", "unavailable", "degraded"}:
        clean_status = None
    filtered: list[dict[str, Any]] = []
    for source in source_rows:
        if clean_source_type and _clean_filter(source.get("source_type")) != clean_source_type:
            continue
        if clean_status and _clean_filter(source.get("status")) != clean_status:
            continue
        filtered.append(source)
    return filtered[: _clamp_limit(limit)]


def _count_by(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        key = str(item.get(field) or "unknown").strip() or "unknown"
        counts[key] += 1
    return dict(sorted(counts.items()))


def _count_stale_provider_snapshots(snapshots: list[dict[str, Any]], now: datetime) -> int:
    threshold = now - timedelta(hours=DEFAULT_STALE_PROVIDER_HOURS)
    stale = 0
    for snapshot in snapshots:
        observed_at = _parse_datetime(snapshot.get("observed_at"))
        if observed_at is not None and observed_at < threshold:
            stale += 1
    return stale


def _satellite_snapshots_with_stale_state(snapshots: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    threshold = now - timedelta(minutes=DEFAULT_STALE_SATELLITE_MINUTES)
    enriched: list[dict[str, Any]] = []
    for snapshot in snapshots:
        item = dict(snapshot)
        observed_at = _parse_datetime(snapshot.get("observed_at"))
        if observed_at is None:
            item["is_stale"] = False
            item["stale_unknown"] = True
        else:
            item["is_stale"] = observed_at < threshold
        enriched.append(item)
    return enriched


def _count_stale_satellite_snapshots(snapshots: list[dict[str, Any]]) -> int:
    return sum(1 for snapshot in snapshots if bool(snapshot.get("is_stale")))


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _ensure_aware_utc(parsed)


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_filter(value: Any) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))
