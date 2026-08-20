from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import safe_record_event
from .schema import ensure_schema
from .sources import get_source
from .store import DB_PATH, transaction
from .taxonomy import validate_event_type


logger = logging.getLogger("oracle-brain.memory.satellite_activity")

VALID_SATELLITE_EVENT_TYPES = {
    "satellite_started",
    "satellite_stopped",
    "satellite_error",
    "wake_detected",
    "audio_capture_failed",
    "stt_upload_failed",
    "tts_playback_failed",
}

VALID_SATELLITE_STATUSES = {"available", "degraded", "unavailable"}

EVENT_STATUS = {
    "satellite_started": "available",
    "satellite_stopped": "unavailable",
    "satellite_error": "degraded",
    "wake_detected": "available",
    "audio_capture_failed": "degraded",
    "stt_upload_failed": "degraded",
    "tts_playback_failed": "degraded",
}

EVENT_SEVERITY = {
    "satellite_started": "info",
    "satellite_stopped": "info",
    "satellite_error": "error",
    "wake_detected": "info",
    "audio_capture_failed": "warning",
    "stt_upload_failed": "error",
    "tts_playback_failed": "error",
}


@dataclass(frozen=True)
class SatelliteStatusQuery:
    source_id: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def observe_satellite_activity(
    *,
    source_id: str,
    event_type: str | None = None,
    status: str | None = None,
    correlation_id: str | None = None,
    observed_at: str | None = None,
    payload: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = db_path or DB_PATH
    clean_source_id = _clean_required(source_id, "source_id")
    clean_event_type = _clean_event_type(event_type)
    clean_status = _clean_status(status or (EVENT_STATUS.get(clean_event_type or "") if clean_event_type else None))
    clean_correlation_id = _clean_optional(correlation_id)
    clean_observed_at = _clean_optional(observed_at) or utc_now_iso()
    clean_payload = dict(payload or {})
    clean_snapshot = dict(snapshot or {})
    if clean_event_type is None and not clean_snapshot:
        raise ValueError("satellite activity requires event_type or snapshot")
    if clean_event_type is None and clean_status is None:
        clean_status = _clean_status(clean_snapshot.get("status"))
    if clean_status is None:
        clean_status = "available"

    ensure_schema(path, copy_provisional_suggestions=False)
    _ensure_satellite_source(clean_source_id, db_path=path)

    snapshot_result = None
    if clean_snapshot or clean_status:
        snapshot_result = _upsert_satellite_snapshot(
            source_id=clean_source_id,
            status=clean_status,
            correlation_id=clean_correlation_id,
            observed_at=clean_observed_at,
            event_type=clean_event_type,
            payload=clean_payload,
            snapshot=clean_snapshot,
            db_path=path,
        )

    event_recorded = False
    if clean_event_type is not None:
        event_recorded = safe_record_event(
            clean_event_type,
            severity=EVENT_SEVERITY[clean_event_type],
            observed_at=clean_observed_at,
            source_id=clean_source_id,
            correlation_id=clean_correlation_id,
            provider=clean_source_id,
            domain="satellite",
            status=clean_status,
            payload={
                **clean_payload,
                "projection_id": satellite_status_projection_id(clean_source_id),
            },
            db_path=path,
        )

    return {
        "source_id": clean_source_id,
        "event_type": clean_event_type,
        "status": clean_status,
        "event_recorded": event_recorded,
        "snapshot": snapshot_result,
    }


def safe_observe_satellite_activity(**kwargs: Any) -> bool:
    try:
        observe_satellite_activity(**kwargs)
    except Exception as exc:
        logger.warning(
            "satellite_activity_observation_failed source_id=%s event_type=%s detail=%s",
            kwargs.get("source_id") or "-",
            kwargs.get("event_type") or "-",
            exc,
        )
        return False
    return True


def satellite_status_projection_id(source_id: str) -> str:
    return f"satellite_status:{_snapshot_part(_clean_required(source_id, 'source_id'))}"


def get_satellite_status_snapshot(source_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    return get_latest_satellite_status(source_id, db_path=db_path)


def get_latest_satellite_status(source_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    clean_source_id = _clean_required(source_id, "source_id")
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_current_projections WHERE projection_id = ? AND projection_type = ?",
            (satellite_status_projection_id(clean_source_id), "satellite_status"),
        ).fetchone()
    return _row_to_snapshot(row) if row else None


def query_satellite_status_snapshots(
    query: SatelliteStatusQuery | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    query = query or SatelliteStatusQuery()
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    sql = "SELECT * FROM memory_current_projections WHERE projection_type = ?"
    args: list[Any] = ["satellite_status"]
    source_id = _clean_required(query.source_id, "source_id") if query.source_id else None
    status = _clean_optional(query.status)
    if status is not None and status not in VALID_SATELLITE_STATUSES:
        raise ValueError(f"Unsupported satellite status: {query.status!r}")
    if source_id:
        sql += " AND source_id = ?"
        args.append(source_id)
    if status:
        sql += " AND status = ?"
        args.append(status)
    limit = _clamp_limit(query.limit)
    offset = max(0, int(query.offset or 0))
    sql += " ORDER BY observed_at DESC, projection_id ASC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def list_satellite_status_snapshots(
    *,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    return query_satellite_status_snapshots(SatelliteStatusQuery(limit=limit), db_path=db_path)


def _upsert_satellite_snapshot(
    *,
    source_id: str,
    status: str,
    correlation_id: str | None,
    observed_at: str,
    event_type: str | None,
    payload: dict[str, Any],
    snapshot: dict[str, Any],
    db_path: Path,
) -> dict[str, Any] | None:
    projection_id = satellite_status_projection_id(source_id)
    now = utc_now_iso()
    snapshot_payload = dict(snapshot)
    snapshot_payload.pop("status", None)
    snapshot_payload.setdefault("last_seen_at", observed_at)
    if event_type:
        snapshot_payload["last_event_type"] = event_type
    if event_type == "wake_detected":
        snapshot_payload.setdefault("last_wake_at", observed_at)
    if status in {"degraded", "unavailable"}:
        snapshot_payload.setdefault("last_error", _last_error(event_type=event_type, payload=payload))
    else:
        snapshot_payload.setdefault("last_error", None)

    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_current_projections (
                projection_id, created_at, updated_at, observed_at, projection_type,
                source_id, provider, domain, status, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(projection_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                observed_at = excluded.observed_at,
                projection_type = excluded.projection_type,
                source_id = excluded.source_id,
                provider = excluded.provider,
                domain = excluded.domain,
                status = excluded.status,
                correlation_id = excluded.correlation_id,
                payload_json = excluded.payload_json
            """,
            (
                projection_id,
                now,
                now,
                observed_at,
                "satellite_status",
                source_id,
                source_id,
                "satellite",
                status,
                correlation_id,
                json.dumps(snapshot_payload, sort_keys=True),
            ),
        )
    return get_satellite_status_snapshot(source_id, db_path=db_path)


def _ensure_satellite_source(source_id: str, *, db_path: Path) -> None:
    source = get_source(source_id, db_path=db_path)
    if source is None or str(source.get("source_type") or "") != "satellite":
        raise ValueError(f"Unknown satellite source_id: {source_id!r}")


def _clean_event_type(event_type: str | None) -> str | None:
    cleaned = _clean_optional(event_type)
    if cleaned is None:
        return None
    validate_event_type(cleaned)
    if cleaned not in VALID_SATELLITE_EVENT_TYPES:
        raise ValueError(f"Unsupported satellite activity event type: {event_type!r}")
    return cleaned


def _clean_status(status: Any) -> str | None:
    cleaned = _clean_optional(status)
    if cleaned is None:
        return None
    if cleaned not in VALID_SATELLITE_STATUSES:
        raise ValueError(f"Unsupported satellite status: {status!r}")
    return cleaned


def _clean_required(value: str | None, field_name: str) -> str:
    cleaned = _clean_optional(value)
    if cleaned is None:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _clean_optional(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _snapshot_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value).strip())


def _last_error(*, event_type: str | None, payload: dict[str, Any]) -> str | None:
    for key in ("error", "detail", "reason", "stop_reason"):
        value = _clean_optional(payload.get(key))
        if value:
            return value
    return event_type


def _row_to_snapshot(row: Any) -> dict[str, Any]:
    return {
        "projection_id": row["projection_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "observed_at": row["observed_at"],
        "projection_type": row["projection_type"],
        "source_id": row["source_id"],
        "provider": row["provider"],
        "domain": row["domain"],
        "status": row["status"],
        "correlation_id": row["correlation_id"],
        "payload": _parse_payload_json(row["payload_json"]),
    }


def _parse_payload_json(value: str | None) -> dict[str, Any]:
    raw = value or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    if not isinstance(parsed, dict):
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    return parsed
