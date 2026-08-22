from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


VALID_SOURCE_TYPES = {"brain", "system", "api", "ui", "satellite", "provider", "background"}

INTERNAL_SOURCE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "source_id": "brain",
        "source_type": "brain",
        "display_name": "Oracle Brain",
        "payload": {"internal": True},
    },
    {
        "source_id": "system",
        "source_type": "system",
        "display_name": "System",
        "payload": {"internal": True},
    },
    {
        "source_id": "api",
        "source_type": "api",
        "display_name": "API",
        "payload": {"internal": True},
    },
    {
        "source_id": "ui",
        "source_type": "ui",
        "display_name": "UI",
        "payload": {"internal": True},
    },
    {
        "source_id": "background",
        "source_type": "background",
        "display_name": "Background Tasks",
        "payload": {"internal": True},
    },
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_internal_sources() -> list[dict[str, Any]]:
    return [
        {
            "source_id": definition["source_id"],
            "source_type": definition["source_type"],
            "display_name": definition["display_name"],
            "payload": dict(definition.get("payload") or {}),
        }
        for definition in INTERNAL_SOURCE_DEFINITIONS
    ]


def source_definitions_from_registry(registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    if not isinstance(registry, dict):
        return definitions

    for raw_source_id, raw_entry in sorted(registry.items(), key=lambda item: str(item[0])):
        source_id = str(raw_source_id or "").strip()
        if not source_id or not isinstance(raw_entry, dict):
            continue
        source_type = str(raw_entry.get("source_type") or "").strip().lower()
        if source_type != "satellite":
            continue

        display_name = str(raw_entry.get("display_name") or source_id).strip() or source_id
        payload: dict[str, Any] = {
            "fixed": bool(raw_entry.get("fixed", False)),
            "source_registry": True,
        }
        default_room = str(raw_entry.get("default_room") or "").strip().lower()
        if default_room:
            payload["default_room"] = default_room

        definitions.append(
            {
                "source_id": source_id,
                "source_type": source_type,
                "display_name": display_name,
                "payload": payload,
            }
        )

    return definitions


def seed_sources(
    definitions: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    seeded: list[dict[str, Any]] = []
    for definition in definitions:
        seeded.append(
            upsert_source(
                source_id=str(definition["source_id"]),
                source_type=str(definition["source_type"]),
                display_name=str(definition["display_name"]),
                status=str(definition.get("status") or "active"),
                payload=dict(definition.get("payload") or {}),
                db_path=db_path,
            )
        )
    return seeded


def seed_default_sources(
    *,
    source_registry: dict[str, dict[str, Any]] | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    definitions = default_internal_sources()
    if source_registry is not None:
        definitions.extend(source_definitions_from_registry(source_registry))
    return seed_sources(definitions, db_path=db_path)


def upsert_source(
    *,
    source_id: str,
    source_type: str,
    display_name: str,
    status: str = "active",
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"Unknown Oracle Memory source type: {source_type!r}")
    if status not in {"active", "disabled", "retired"}:
        raise ValueError(f"Unknown Oracle Memory source status: {status!r}")
    path = db_path or DB_PATH
    ensure_schema(path)
    now = utc_now_iso()
    payload_json = json.dumps(payload or {}, sort_keys=True)
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_sources (
                source_id, created_at, updated_at, source_type, display_name, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                source_type = excluded.source_type,
                display_name = excluded.display_name,
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (source_id, now, now, source_type, display_name, status, payload_json),
        )
    source = get_source(source_id, db_path=path)
    if source is None:
        raise RuntimeError(f"Failed to load Oracle Memory source {source_id}")
    return source


def get_source(source_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM memory_sources WHERE source_id = ?", (source_id,)).fetchone()
    return _row_to_source(row) if row else None


def list_sources(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute("SELECT * FROM memory_sources ORDER BY created_at, source_id").fetchall()
    return [_row_to_source(row) for row in rows]


def _row_to_source(row: Any) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source_type": row["source_type"],
        "display_name": row["display_name"],
        "status": row["status"],
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
