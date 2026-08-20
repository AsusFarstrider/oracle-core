from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .correlation import get_correlation_id
from .events import list_events
from .runtime import safe_record_event
from .schema import ensure_schema
from .store import DB_PATH, transaction


logger = logging.getLogger("oracle-brain.memory.provider_status")

VALID_PROVIDER_STATUSES = {"available", "unavailable", "degraded", "disabled"}


PROVIDER_DEFINITIONS: dict[str, dict[str, str]] = {
    "home_assistant": {"provider": "home_assistant", "domain": "home_assistant"},
    "ollama": {"provider": "ollama", "domain": "ollama"},
    "stt": {"provider": "stt", "domain": "stt"},
    "tts": {"provider": "tts", "domain": "tts"},
    "audiobookshelf": {"provider": "audiobookshelf", "domain": "audiobook"},
    "calendar": {"provider": "calendar", "domain": "calendar"},
    "librenms": {"provider": "librenms", "domain": "network"},
    "music": {"provider": "plex", "domain": "music"},
}


@dataclass(frozen=True)
class ProviderStatusQuery:
    provider: str | None = None
    domain: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_provider_health(provider_key: str, response: Any) -> dict[str, Any]:
    key = _normalize_key(provider_key)
    definition = PROVIDER_DEFINITIONS.get(key)
    if definition is None:
        raise ValueError(f"Unknown provider health key: {provider_key!r}")
    data = _response_to_dict(response)
    status = _normalize_status(key, data)
    provider = str(data.get("provider") or definition["provider"]).strip() or definition["provider"]
    return {
        "provider": provider,
        "domain": definition["domain"],
        "status": status,
        "source_id": None,
        "payload": _payload_for_provider(key, data, provider, status),
    }


def observe_provider_status(
    *,
    provider: str,
    domain: str,
    status: str,
    source_id: str | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    normalized_status = _clean_status(status)
    normalized_provider = _clean_required(provider, "provider")
    normalized_domain = _clean_required(domain, "domain")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    projection_id = provider_status_projection_id(normalized_provider, normalized_domain)
    now = utc_now_iso()
    resolved_correlation_id = correlation_id or get_correlation_id()
    resolved_payload = dict(payload or {})

    with transaction(path) as conn:
        previous = conn.execute(
            "SELECT * FROM memory_current_projections WHERE projection_id = ?",
            (projection_id,),
        ).fetchone()
        previous_status = previous["status"] if previous else None
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
                now,
                "provider_status",
                source_id,
                normalized_provider,
                normalized_domain,
                normalized_status,
                resolved_correlation_id,
                json.dumps(resolved_payload, sort_keys=True),
            ),
        )

    event_type = _event_type_for_transition(previous_status, normalized_status)
    event_recorded = False
    if event_type is not None:
        event_recorded = safe_record_event(
            event_type,
            severity=_severity_for_status(normalized_status),
            source_id=source_id,
            correlation_id=resolved_correlation_id,
            provider=normalized_provider,
            domain=normalized_domain,
            status=normalized_status,
            payload={
                **resolved_payload,
                "previous_status": previous_status,
                "projection_id": projection_id,
            },
            db_path=path,
        )

    snapshot = get_provider_status_snapshot(normalized_provider, normalized_domain, db_path=path)
    return {
        "projection_id": projection_id,
        "previous_status": previous_status,
        "status": normalized_status,
        "event_type": event_type,
        "event_recorded": event_recorded,
        "snapshot": snapshot,
    }


def safe_observe_provider_health(
    provider_key: str,
    response: Any,
    *,
    db_path: Path | None = None,
) -> bool:
    try:
        observation = normalize_provider_health(provider_key, response)
        observe_provider_status(
            provider=observation["provider"],
            domain=observation["domain"],
            status=observation["status"],
            source_id=observation.get("source_id"),
            payload=observation["payload"],
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("provider_status_observation_failed provider_key=%s detail=%s", provider_key, exc)
        return False
    return True


def provider_status_projection_id(provider: str, domain: str) -> str:
    return f"provider_status:{_snapshot_part(domain)}:{_snapshot_part(provider)}"


def get_provider_status_snapshot(
    provider: str,
    domain: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    return get_latest_provider_status(provider, domain, db_path=db_path)


def get_latest_provider_status(
    provider: str,
    domain: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_provider = _clean_required(provider, "provider")
    clean_domain = _clean_filter(domain)
    if clean_domain:
        projection_id = provider_status_projection_id(clean_provider, clean_domain)
        with transaction(path) as conn:
            row = conn.execute(
                "SELECT * FROM memory_current_projections WHERE projection_id = ? AND projection_type = ?",
                (projection_id, "provider_status"),
            ).fetchone()
        return _row_to_snapshot(row) if row else None
    results = query_provider_status_snapshots(
        ProviderStatusQuery(provider=clean_provider, limit=1),
        db_path=path,
    )
    return results[0] if results else None


def query_provider_status_snapshots(
    query: ProviderStatusQuery | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    query = query or ProviderStatusQuery()
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    sql = "SELECT * FROM memory_current_projections WHERE projection_type = ?"
    args: list[Any] = ["provider_status"]
    filters = {
        "provider": _clean_filter(query.provider),
        "domain": _clean_filter(query.domain),
        "status": _clean_filter(query.status),
    }
    if filters["status"] and filters["status"] not in VALID_PROVIDER_STATUSES:
        raise ValueError(f"Unknown Oracle Memory provider status: {query.status!r}")
    for column, value in filters.items():
        if value:
            sql += f" AND {column} = ?"
            args.append(value)
    limit = _clamp_limit(query.limit)
    offset = _clean_offset(query.offset)
    sql += " ORDER BY observed_at DESC, projection_id ASC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def list_provider_status_snapshots(
    *,
    db_path: Path | None = None,
    provider: str | None = None,
    domain: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    return query_provider_status_snapshots(
        ProviderStatusQuery(provider=provider, domain=domain, status=status),
        db_path=db_path,
    )


def list_provider_status_events(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event_type in (
        "provider_available",
        "provider_unavailable",
        "provider_degraded",
        "provider_recovered",
    ):
        events.extend(list_events(db_path=db_path, event_type=event_type))
    return sorted(events, key=lambda item: (item["observed_at"], item["event_id"]), reverse=True)


def _normalize_status(provider_key: str, data: dict[str, Any]) -> str:
    raw_status = str(data.get("status") or "").strip().lower()
    if raw_status == "disabled":
        return "disabled"
    if provider_key in {"stt", "tts"}:
        return "available" if raw_status == "ok" and bool(data.get("available")) else "unavailable"
    if provider_key == "librenms":
        if raw_status == "ok" and bool(data.get("available")):
            return "degraded" if bool(data.get("degraded")) else "available"
        return "unavailable"
    return "available" if raw_status == "ok" else "unavailable"


def _payload_for_provider(
    provider_key: str,
    data: dict[str, Any],
    provider: str,
    status: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider_key": provider_key,
        "provider": provider,
        "normalized_status": status,
        "service": _clean_optional(data.get("service")),
        "detail_classification": _classify_detail(data),
    }
    if provider_key == "home_assistant":
        payload["http_status"] = data.get("http_status")
        payload["home_assistant_url_present"] = bool(data.get("home_assistant_url"))
    elif provider_key == "ollama":
        payload["http_status"] = data.get("http_status")
        payload["model"] = _clean_optional(data.get("model"))
        payload["ollama_url_present"] = bool(data.get("ollama_url"))
    elif provider_key in {"stt", "tts"}:
        payload["configured"] = bool(data.get("configured"))
        payload["available"] = bool(data.get("available"))
    elif provider_key == "audiobookshelf":
        payload["audiobookshelf_configured"] = bool(data.get("audiobookshelf_configured"))
        payload["configured_satellite_count"] = len(data.get("configured_satellites") or [])
    elif provider_key == "calendar":
        payload["calendar_configured"] = bool(data.get("calendar_configured"))
        payload["timezone_present"] = bool(data.get("timezone"))
    elif provider_key == "librenms":
        payload["configured"] = bool(data.get("configured"))
        payload["available"] = bool(data.get("available"))
        payload["degraded"] = bool(data.get("degraded"))
        payload["http_status"] = data.get("http_status")
        payload["active_alert_count"] = data.get("active_alert_count")
        payload["missing_config_keys"] = list(data.get("missing_config_keys") or [])
    elif provider_key == "music":
        payload["plex_configured"] = bool(data.get("plex_configured"))
        payload["configured_satellite_count"] = len(data.get("configured_satellites") or [])
    return {key: value for key, value in payload.items() if value is not None}


def _event_type_for_transition(previous_status: str | None, new_status: str) -> str | None:
    if previous_status == new_status:
        return None
    if new_status == "available":
        return "provider_available" if previous_status is None else "provider_recovered"
    if new_status == "degraded":
        return "provider_degraded"
    if new_status == "disabled":
        return None
    return "provider_unavailable"


def _severity_for_status(status: str) -> str:
    if status in {"available", "disabled"}:
        return "info"
    if status == "degraded":
        return "warning"
    return "error"


def _classify_detail(data: dict[str, Any]) -> str:
    raw_status = str(data.get("status") or "").strip().lower()
    if raw_status in {"ok", "disabled"}:
        return raw_status
    detail = str(data.get("detail") or "").lower()
    if "missing" in detail or "not configured" in detail or "config" in detail:
        return "config_missing"
    if data.get("http_status"):
        return "http_error"
    if "connection" in detail or "refused" in detail or "timed out" in detail or "timeout" in detail:
        return "connection_error"
    if "unavailable" in detail:
        return "provider_unavailable"
    return "unknown_failure"


def _response_to_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        data = response.model_dump()
    elif hasattr(response, "dict"):
        data = response.dict()
    elif isinstance(response, dict):
        data = dict(response)
    else:
        data = {
            key: getattr(response, key)
            for key in dir(response)
            if not key.startswith("_") and not callable(getattr(response, key))
        }
    return data if isinstance(data, dict) else {}


def _clean_status(status: str) -> str:
    cleaned = str(status or "").strip().lower()
    if cleaned not in VALID_PROVIDER_STATUSES:
        raise ValueError(f"Unknown Oracle Memory provider status: {status!r}")
    return cleaned


def _clean_required(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        raise ValueError(f"Missing Oracle Memory provider status field: {field_name}")
    return cleaned


def _clean_optional(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clean_filter(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _clean_offset(offset: int) -> int:
    try:
        parsed = int(offset)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, parsed)


def _snapshot_part(value: str) -> str:
    cleaned = str(value or "").strip().lower().replace(" ", "_").replace("/", "_")
    return cleaned or "unknown"


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


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
