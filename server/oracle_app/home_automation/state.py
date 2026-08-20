from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oracle_app.memory.schema import ensure_schema
from oracle_app.memory.store import DB_PATH, transaction


def observe_canonical_state(
    *,
    subject: str,
    event_id: str,
    state: str,
    observed_at: datetime,
    db_path: Path | None = None,
) -> bool:
    """Persist the newest canonical subject state; reject strictly older evidence."""
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    projection_id = f"home_automation:{subject}"
    normalized_observed_at = (
        observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    ).astimezone(UTC)
    observed_iso = normalized_observed_at.isoformat()
    now_iso = datetime.now(UTC).isoformat()
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT observed_at, payload_json FROM memory_current_projections WHERE projection_id = ?",
            (projection_id,),
        ).fetchone()
        if row is not None:
            previous_at = _parse_datetime(row["observed_at"])
            previous_payload = _payload(row["payload_json"])
            if previous_payload.get("event_id") == event_id:
                return False
            if previous_at is not None and normalized_observed_at < previous_at:
                return False
        conn.execute(
            """
            INSERT INTO memory_current_projections (
                projection_id, created_at, updated_at, observed_at, projection_type,
                provider, domain, status, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(projection_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                observed_at = excluded.observed_at,
                status = excluded.status,
                correlation_id = excluded.correlation_id,
                payload_json = excluded.payload_json
            """,
            (
                projection_id,
                now_iso,
                now_iso,
                observed_iso,
                "home_automation_canonical_state",
                "home_assistant",
                "home_automation",
                state,
                event_id,
                json.dumps({"event_id": event_id, "subject": subject, "state": state}, sort_keys=True),
            ),
        )
    return True


def list_canonical_states(
    *,
    db_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the latest canonical home-automation state by subject."""
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        rows = conn.execute(
            """
            SELECT projection_id, observed_at, status, correlation_id, payload_json
            FROM memory_current_projections
            WHERE projection_type = 'home_automation_canonical_state'
            ORDER BY observed_at DESC, projection_id ASC
            """
        ).fetchall()
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _payload(row["payload_json"])
        subject = str(payload.get("subject") or "").strip()
        if not subject:
            projection_id = str(row["projection_id"] or "")
            subject = projection_id.removeprefix("home_automation:")
        if not subject:
            continue
        states[subject] = {
            "subject": subject,
            "state": str(payload.get("state") or row["status"] or ""),
            "event_id": str(payload.get("event_id") or row["correlation_id"] or ""),
            "observed_at": str(row["observed_at"] or ""),
        }
    return states


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _payload(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
