from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


_PROJECTION_TYPE = "orchestration_trigger_evidence"


def observe_trigger_transition(
    *,
    kind: str,
    evidence_id: str,
    state: str,
    observed_at: str,
    occurrence_id: str | None = None,
    emit_initial: bool = False,
    db_path: Path | None = None,
) -> dict[str, str] | None:
    """Persist one normalized transition before returning it for delivery."""

    path = db_path or DB_PATH
    ensure_schema(path)
    projection_id = _projection_id(kind, evidence_id)
    now = datetime.now(timezone.utc).isoformat()
    with transaction(path) as conn:
        previous = conn.execute(
            "SELECT status, payload_json FROM memory_current_projections WHERE projection_id=?",
            (projection_id,),
        ).fetchone()
        payload = _payload(previous["payload_json"] if previous is not None else "{}")
        pending = payload.get("pending")
        if isinstance(pending, dict) and pending.get("occurrence_id"):
            return {key: str(value) for key, value in pending.items()}
        previous_state = str(previous["status"]) if previous is not None else ""
        occurrence = None
        if (previous is not None and previous_state != state) or (previous is None and emit_initial):
            raw = f"{kind}\0{evidence_id}\0{previous_state}\0{state}\0{observed_at}".encode()
            occurrence = {
                "kind": kind,
                "evidence_id": evidence_id,
                "state": state,
                "previous_state": previous_state,
                "occurrence_id": occurrence_id or "trigger-evidence:" + hashlib.sha256(raw).hexdigest(),
                "observed_at": observed_at,
            }
        conn.execute(
            """
            INSERT INTO memory_current_projections (
                projection_id, created_at, updated_at, observed_at, projection_type,
                source_id, provider, domain, status, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, 'orchestration', ?, NULL, ?)
            ON CONFLICT(projection_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                observed_at=excluded.observed_at,
                status=excluded.status,
                payload_json=excluded.payload_json
            """,
            (
                projection_id,
                now,
                now,
                observed_at,
                _PROJECTION_TYPE,
                kind,
                state,
                json.dumps({"pending": occurrence}, sort_keys=True),
            ),
        )
    return occurrence


def pending_trigger_transition(
    *,
    kind: str,
    evidence_id: str,
    db_path: Path | None = None,
) -> dict[str, str] | None:
    """Return only a previously persisted, not-yet-delivered occurrence."""

    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT payload_json FROM memory_current_projections WHERE projection_id=?",
            (_projection_id(kind, evidence_id),),
        ).fetchone()
    if current is None:
        return None
    pending = _payload(current["payload_json"]).get("pending")
    if not isinstance(pending, dict) or not pending.get("occurrence_id"):
        return None
    return {key: str(value) for key, value in pending.items()}


def complete_trigger_transition(
    occurrence_id: str,
    *,
    kind: str,
    evidence_id: str,
    db_path: Path | None = None,
) -> None:
    path = db_path or DB_PATH
    ensure_schema(path)
    projection_id = _projection_id(kind, evidence_id)
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT payload_json FROM memory_current_projections WHERE projection_id=?",
            (projection_id,),
        ).fetchone()
        if current is None:
            return
        pending = _payload(current["payload_json"]).get("pending")
        if not isinstance(pending, dict) or str(pending.get("occurrence_id") or "") != occurrence_id:
            return
        conn.execute(
            "UPDATE memory_current_projections SET updated_at=?, payload_json='{}' WHERE projection_id=?",
            (datetime.now(timezone.utc).isoformat(), projection_id),
        )


def _projection_id(kind: str, evidence_id: str) -> str:
    return f"orchestration-trigger:{kind}:{evidence_id}"


def _payload(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
