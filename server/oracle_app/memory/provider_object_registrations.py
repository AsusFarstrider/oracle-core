from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


_TYPE = "provider_object_registration"


def store_provider_object_registration(*, domain: str, canonical_id: str, display_name: str, aliases: list[str], user_ids: list[str], provider: str, provider_object_id: str, db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    ensure_schema(path)
    now = datetime.now(UTC).isoformat()
    payload = {
        "canonical_id": canonical_id,
        "display_name": display_name,
        "aliases": aliases,
        "user_ids": user_ids,
        "provider_object_id": provider_object_id,
    }
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO memory_current_projections (
                projection_id, created_at, updated_at, observed_at, projection_type,
                source_id, provider, domain, status, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'active', NULL, ?)
            ON CONFLICT(projection_id) DO UPDATE SET updated_at=excluded.updated_at,
              observed_at=excluded.observed_at, status='active', payload_json=excluded.payload_json""",
            (f"provider-object:{provider}:{domain}:{canonical_id}", now, now, now, _TYPE, provider, domain, json.dumps(payload, sort_keys=True)),
        )


def load_provider_object_registrations(domain: str, *, provider: str | None = None, db_path: Path | None = None) -> tuple[dict[str, Any], ...]:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM memory_current_projections WHERE projection_type=? AND domain=? AND status='active' AND (? IS NULL OR provider=?) ORDER BY projection_id",
            (_TYPE, domain, provider, provider),
        ).fetchall()
    values: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            values.append(value)
    return tuple(values)


def remove_provider_object_registration(domain: str, canonical_id: str, *, provider: str, db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        conn.execute(
            "UPDATE memory_current_projections SET status='deleted', updated_at=? WHERE projection_id IN (?, ?) AND projection_type=? AND provider=?",
            (datetime.now(UTC).isoformat(), f"provider-object:{provider}:{domain}:{canonical_id}", f"provider-object:{domain}:{canonical_id}", _TYPE, provider),
        )
