from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from oracle_app.runtime_paths import RUNTIME_PATHS
from oracle_app.memory.schema import SUGGESTIONS_SCHEMA


DATA_DIR = RUNTIME_PATHS.data
DB_PATH = RUNTIME_PATHS.memory_database


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def ensure_storage(db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # Oracle Memory is the sole table/schema authority for Suggestions.
        conn.executescript(SUGGESTIONS_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = db_path or DB_PATH
    ensure_storage(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_run(
    *,
    run_type: str,
    window_start: str,
    window_end: str,
    reason: str | None,
    custom_prompt: str | None,
    mock: bool,
) -> str:
    run_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO suggestion_runs (
                run_id, created_at, status, run_type, window_start, window_end,
                reason, custom_prompt, mock
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, utc_now_iso(), "running", run_type, window_start, window_end, reason, custom_prompt, int(mock)),
        )
    return run_id


def update_run(
    run_id: str,
    *,
    status: str,
    openclaw_status: str,
    collector_status: dict[str, Any],
    error: str | None,
    suggestion_count: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE suggestion_runs
            SET completed_at = ?, status = ?, openclaw_status = ?, collector_status_json = ?,
                error = ?, packet_path = NULL, response_path = NULL, suggestion_count = ?
            WHERE run_id = ?
            """,
            (
                utc_now_iso(),
                status,
                openclaw_status,
                json.dumps(collector_status, sort_keys=True),
                error,
                int(suggestion_count),
                run_id,
            ),
        )


def last_successful_run_end() -> str | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT window_end FROM suggestion_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["window_end"]) if row else None


def save_current_exchange(
    run_id: str,
    *,
    packet: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM suggestion_exchange_current WHERE singleton_id=1"
        ).fetchone()
        packet_json = (
            json.dumps(packet, sort_keys=True) if packet is not None
            else (existing["packet_json"] if existing is not None and existing["run_id"] == run_id else None)
        )
        response_json = (
            json.dumps(response, sort_keys=True) if response is not None
            else (existing["response_json"] if existing is not None and existing["run_id"] == run_id else None)
        )
        conn.execute(
            """INSERT INTO suggestion_exchange_current (
                   singleton_id, run_id, updated_at, packet_json, response_json
               ) VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                   run_id=excluded.run_id, updated_at=excluded.updated_at,
                   packet_json=excluded.packet_json, response_json=excluded.response_json""",
            (run_id, utc_now_iso(), packet_json, response_json),
        )


def get_current_exchange() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM suggestion_exchange_current WHERE singleton_id=1"
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "updated_at": row["updated_at"],
        "packet": json.loads(row["packet_json"]) if row["packet_json"] else None,
        "response": json.loads(row["response_json"]) if row["response_json"] else None,
    }


def default_window() -> tuple[str, str]:
    end = datetime.now().astimezone()
    start_text = last_successful_run_end()
    if start_text:
        return start_text, end.isoformat()
    return (end - timedelta(days=7)).isoformat(), end.isoformat()


def similarity_key(category: str, source: str, title: str, action: str) -> str:
    text = " ".join(str(part or "") for part in (category, source, title, action)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())[:240]


def find_similar(sim_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM suggestions
            WHERE similarity_key = ? AND status IN ('rejected', 'corrected', 'ignored', 'false_positive')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (sim_key,),
        ).fetchone()
    return row_to_suggestion(row) if row else None


def insert_suggestions(run_id: str, items: list[dict[str, Any]], *, mock: bool) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    with connect() as conn:
        for item in items:
            item_id = uuid.uuid4().hex
            sim_key = similarity_key(
                str(item.get("category") or "unknown"),
                str(item.get("source") or "mixed"),
                str(item.get("title") or ""),
                str(item.get("suggested_action") or ""),
            )
            similar = find_similar(sim_key)
            row_values = {
                "id": item_id,
                "run_id": run_id,
                "created_at": utc_now_iso(),
                "status": "new",
                "title": str(item.get("title") or "Untitled suggestion"),
                "severity": str(item.get("severity") or "info"),
                "category": str(item.get("category") or "unknown"),
                "source": str(item.get("source") or "mixed"),
                "summary": str(item.get("summary") or ""),
                "evidence_json": json.dumps(item.get("evidence") or []),
                "suggested_action": str(item.get("suggested_action") or ""),
                "recommended_oracle_action": item.get("recommended_oracle_action"),
                "confidence": float(item.get("confidence") or 0.0),
                "requires_review": int(bool(item.get("requires_review", True))),
                "similarity_key": sim_key,
                "similar_to_id": similar["id"] if similar else None,
                "raw_openclaw_item_json": json.dumps(item, sort_keys=True),
                "mock": int(mock),
            }
            conn.execute(
                """
                INSERT INTO suggestions (
                    id, run_id, created_at, status, title, severity, category, source,
                    summary, evidence_json, suggested_action, recommended_oracle_action,
                    confidence, requires_review, similarity_key, similar_to_id,
                    raw_openclaw_item_json, mock
                ) VALUES (
                    :id, :run_id, :created_at, :status, :title, :severity, :category, :source,
                    :summary, :evidence_json, :suggested_action, :recommended_oracle_action,
                    :confidence, :requires_review, :similarity_key, :similar_to_id,
                    :raw_openclaw_item_json, :mock
                )
                """,
                row_values,
            )
            created.append(row_to_suggestion(row_values))
    return created


def list_suggestions(filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where: list[str] = []
    args: list[str] = []
    for field in ("status", "severity", "source", "category"):
        value = filters.get(field)
        if value:
            where.append(f"{field} = ?")
            args.append(value)
    sql = "SELECT * FROM suggestions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 250"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [row_to_suggestion(row) for row in rows]


def get_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    return row_to_suggestion(row) if row else None


def review_suggestion(suggestion_id: str, review: dict[str, Any]) -> dict[str, Any] | None:
    reviewed_at = utc_now_iso()
    with connect() as conn:
        row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE suggestions
            SET status = ?, reviewed_at = ?, review_decision = ?, review_notes = ?,
                correction_text = ?, rejection_reason = ?, future_automation_candidate = ?,
                suppress_if_repeated = ?
            WHERE id = ?
            """,
            (
                review["status"],
                reviewed_at,
                review["status"],
                review.get("notes"),
                review.get("correction_text"),
                review.get("rejection_reason"),
                int(bool(review.get("future_automation_candidate"))),
                int(bool(review.get("suppress_if_repeated"))),
                suggestion_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO suggestion_reviews (
                review_id, suggestion_id, run_id, reviewed_at, status, notes,
                correction_text, rejection_reason, future_automation_candidate,
                suppress_if_repeated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                suggestion_id,
                row["run_id"],
                reviewed_at,
                review["status"],
                review.get("notes"),
                review.get("correction_text"),
                review.get("rejection_reason"),
                int(bool(review.get("future_automation_candidate"))),
                int(bool(review.get("suppress_if_repeated"))),
            ),
        )
    return get_suggestion(suggestion_id)


def list_runs() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM suggestion_runs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [row_to_run(row) for row in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM suggestion_runs WHERE run_id = ?", (run_id,)).fetchone()
    return row_to_run(row) if row else None


def review_history(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM suggestions
            WHERE status IN ('rejected', 'corrected', 'ignored', 'false_positive')
            ORDER BY reviewed_at DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [row_to_suggestion(row) for row in rows]


def row_to_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "status": row["status"],
        "run_type": row["run_type"],
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "reason": row["reason"],
        "custom_prompt": row["custom_prompt"],
        "openclaw_status": row["openclaw_status"],
        "collector_status": json.loads(row["collector_status_json"] or "{}"),
        "error": row["error"],
        "packet_path": row["packet_path"],
        "response_path": row["response_path"],
        "suggestion_count": row["suggestion_count"],
        "mock": bool(row["mock"]),
    }


def row_to_suggestion(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "title": row["title"],
        "severity": row["severity"],
        "category": row["category"],
        "source": row["source"],
        "summary": row["summary"],
        "evidence": json.loads(row["evidence_json"] or "[]"),
        "suggested_action": row["suggested_action"],
        "recommended_oracle_action": row["recommended_oracle_action"],
        "confidence": row["confidence"],
        "requires_review": bool(row["requires_review"]),
        "similarity_key": row["similarity_key"],
        "similar_to_id": row["similar_to_id"],
        "raw_openclaw_item": json.loads(row["raw_openclaw_item_json"] or "{}"),
        "reviewed_at": row.get("reviewed_at") if isinstance(row, dict) else row["reviewed_at"],
        "review_decision": row.get("review_decision") if isinstance(row, dict) else row["review_decision"],
        "review_notes": row.get("review_notes") if isinstance(row, dict) else row["review_notes"],
        "correction_text": row.get("correction_text") if isinstance(row, dict) else row["correction_text"],
        "rejection_reason": row.get("rejection_reason") if isinstance(row, dict) else row["rejection_reason"],
        "future_automation_candidate": bool(row.get("future_automation_candidate", 0) if isinstance(row, dict) else row["future_automation_candidate"]),
        "suppress_if_repeated": bool(row.get("suppress_if_repeated", 0) if isinstance(row, dict) else row["suppress_if_repeated"]),
        "mock": bool(row["mock"]),
    }
