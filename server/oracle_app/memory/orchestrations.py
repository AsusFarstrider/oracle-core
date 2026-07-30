from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import record_event
from .schema import ensure_schema
from .store import DB_PATH, transaction


logger = logging.getLogger("oracle-brain.memory.orchestrations")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_orchestration_run(
    *,
    run_id: str,
    orchestration_id: str,
    kind: str,
    status: str,
    started_at: str,
    preview_id: str = "",
    digest: str = "",
    client_id: str = "",
    summary: str = "",
    approval_consumed: bool = False,
    definition_domain: str = "",
    definition_version: str = "",
    correlation_key: str = "",
    activation_idempotency_key: str = "",
    controller_version: str = "",
    controller_state: dict[str, Any] | None = None,
    cancellation_reason: str = "",
    cancellation_requester: str = "",
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_orchestration_runs (
                run_id, created_at, updated_at, orchestration_id, kind,
                preview_id, digest, client_id, status, summary, started_at,
                completed_at, approval_consumed, definition_domain,
                definition_version, correlation_key,
                activation_idempotency_key, controller_version,
                controller_state_json, cancellation_reason,
                cancellation_requester, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                now,
                now,
                orchestration_id,
                kind,
                preview_id or None,
                digest or None,
                client_id or None,
                status,
                summary,
                started_at,
                1 if approval_consumed else 0,
                definition_domain,
                definition_version,
                correlation_key or None,
                activation_idempotency_key or None,
                controller_version,
                json.dumps(controller_state or {}, sort_keys=True),
                cancellation_reason,
                cancellation_requester,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )
    run = get_orchestration_run(run_id, db_path=path)
    if run is None:
        raise RuntimeError(f"Failed to load orchestration run {run_id}")
    return run


def upsert_orchestration_step(
    *,
    run_id: str,
    step_id: str,
    ordinal: int,
    status: str,
    target_type: str = "",
    target_id: str = "",
    target_label: str = "",
    action_id: str = "",
    policy_id: str = "",
    summary: str = "",
    request_id: str = "",
    error_class: str = "",
    verification_status: str = "",
    started_at: str = "",
    completed_at: str = "",
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_orchestration_steps (
                run_id, step_id, created_at, updated_at, ordinal, target_type,
                target_id, target_label, action_id, policy_id, status, summary,
                request_id, error_class, verification_status, started_at,
                completed_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, step_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                ordinal = excluded.ordinal,
                target_type = excluded.target_type,
                target_id = excluded.target_id,
                target_label = excluded.target_label,
                action_id = excluded.action_id,
                policy_id = excluded.policy_id,
                status = excluded.status,
                summary = excluded.summary,
                request_id = excluded.request_id,
                error_class = excluded.error_class,
                verification_status = excluded.verification_status,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                payload_json = excluded.payload_json
            """,
            (
                run_id,
                step_id,
                now,
                now,
                max(1, int(ordinal)),
                target_type or None,
                target_id or None,
                target_label or None,
                action_id or None,
                policy_id or None,
                status,
                summary,
                request_id or None,
                error_class or None,
                verification_status or None,
                started_at or None,
                completed_at or None,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )
    step = get_orchestration_step(run_id, step_id, db_path=path)
    if step is None:
        raise RuntimeError(f"Failed to load orchestration step {run_id}:{step_id}")
    return step


def complete_orchestration_run(
    run_id: str,
    *,
    status: str,
    summary: str,
    completed_at: str,
    controller_state: dict[str, Any] | None = None,
    cancellation_reason: str | None = None,
    cancellation_requester: str | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        updates = [
            "updated_at = ?",
            "status = ?",
            "summary = ?",
            "completed_at = ?",
            "payload_json = ?",
        ]
        values: list[Any] = [
            utc_now_iso(),
            status,
            summary,
            completed_at,
            json.dumps(payload or {}, sort_keys=True),
        ]
        if controller_state is not None:
            updates.append("controller_state_json = ?")
            values.append(json.dumps(controller_state, sort_keys=True))
        if cancellation_reason is not None:
            updates.append("cancellation_reason = ?")
            values.append(cancellation_reason)
        if cancellation_requester is not None:
            updates.append("cancellation_requester = ?")
            values.append(cancellation_requester)
        values.append(run_id)
        conn.execute(
            f"UPDATE memory_orchestration_runs SET {', '.join(updates)} WHERE run_id = ?",
            values,
        )
    return get_orchestration_run(run_id, db_path=path)


def update_orchestration_run(
    run_id: str,
    *,
    status: str,
    summary: str,
    controller_state: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        updates = ["updated_at = ?", "status = ?", "summary = ?", "payload_json = ?"]
        values: list[Any] = [
            utc_now_iso(),
            status,
            summary,
            json.dumps(payload or {}, sort_keys=True),
        ]
        if controller_state is not None:
            updates.append("controller_state_json = ?")
            values.append(json.dumps(controller_state, sort_keys=True))
        values.append(run_id)
        conn.execute(
            f"UPDATE memory_orchestration_runs SET {', '.join(updates)} WHERE run_id = ?",
            values,
        )
    return get_orchestration_run(run_id, db_path=path)


def delete_orchestration_run(run_id: str, *, db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        conn.execute("DELETE FROM memory_orchestration_runs WHERE run_id = ?", (run_id,))


def get_orchestration_run(run_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_orchestration_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        step_rows = conn.execute(
            """
            SELECT * FROM memory_orchestration_steps
            WHERE run_id = ?
            ORDER BY ordinal ASC, step_id ASC
            """,
            (run_id,),
        ).fetchall()
    if row is None:
        return None
    return {
        **_run_row(row),
        "steps": [_step_row(step) for step in step_rows],
    }


def get_orchestration_step(
    run_id: str,
    step_id: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM memory_orchestration_steps
            WHERE run_id = ? AND step_id = ?
            """,
            (run_id, step_id),
        ).fetchone()
    return _step_row(row) if row else None


def list_orchestration_runs(
    *,
    orchestration_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    definition_domain: str | None = None,
    correlation_key: str | None = None,
    activation_idempotency_key: str | None = None,
    limit: int = 25,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    bounded_limit = min(100, max(1, int(limit or 25)))
    sql = "SELECT run_id FROM memory_orchestration_runs"
    args: list[Any] = []
    clauses: list[str] = []
    if orchestration_id:
        clauses.append("orchestration_id = ?")
        args.append(str(orchestration_id))
    if kind:
        clauses.append("kind = ?")
        args.append(str(kind))
    if status:
        clauses.append("status = ?")
        args.append(str(status))
    if definition_domain:
        clauses.append("definition_domain = ?")
        args.append(str(definition_domain))
    if correlation_key:
        clauses.append("correlation_key = ?")
        args.append(str(correlation_key))
    if activation_idempotency_key:
        clauses.append("activation_idempotency_key = ?")
        args.append(str(activation_idempotency_key))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
    args.append(bounded_limit)
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        run
        for row in rows
        if (run := get_orchestration_run(str(row["run_id"]), db_path=path)) is not None
    ]


def reconcile_interrupted_orchestration_runs(*, db_path: Path | None = None) -> int:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    with transaction(path) as conn:
        rows = conn.execute(
            "SELECT run_id FROM memory_orchestration_runs WHERE status = 'running'"
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in rows]
        if run_ids:
            conn.executemany(
                """
                UPDATE memory_orchestration_runs
                SET updated_at = ?, status = 'interrupted',
                    summary = 'Oracle restarted before this orchestration completed.',
                    completed_at = ?
                WHERE run_id = ?
                """,
                [(now, now, run_id) for run_id in run_ids],
            )
            conn.executemany(
                """
                UPDATE memory_orchestration_steps
                SET updated_at = ?, status = 'interrupted',
                    summary = CASE
                        WHEN summary = '' THEN 'Oracle restarted before this step completed.'
                        ELSE summary
                    END,
                    completed_at = COALESCE(completed_at, ?)
                WHERE run_id = ? AND status IN ('pending', 'running')
                """,
                [(now, now, run_id) for run_id in run_ids],
            )
    for run_id in run_ids:
        try:
            run = get_orchestration_run(run_id, db_path=path) or {}
            kind = str(run.get("kind") or "")
            record_event(
                "orchestration_routine_interrupted" if kind == "routine" else "orchestration_recovery_interrupted",
                observed_at=now,
                correlation_id=run_id,
                domain="orchestration",
                status="interrupted",
                payload={
                    "run_id": run_id,
                    "orchestration_id": run.get("orchestration_id"),
                    "summary": run.get("summary"),
                },
                db_path=path,
            )
        except Exception as exc:
            logger.warning(
                "orchestration_interruption_event_failed run_id=%s detail=%s",
                run_id,
                exc,
            )
    return len(run_ids)


def safe_reconcile_interrupted_orchestration_runs() -> int:
    try:
        return reconcile_interrupted_orchestration_runs()
    except Exception as exc:
        logger.warning("orchestration_run_reconcile_failed detail=%s", exc)
        return 0


def safe_create_orchestration_run(**kwargs: Any) -> bool:
    try:
        create_orchestration_run(**kwargs)
    except Exception as exc:
        logger.warning("orchestration_run_create_failed detail=%s", exc)
        return False
    return True


def safe_upsert_orchestration_step(**kwargs: Any) -> bool:
    try:
        upsert_orchestration_step(**kwargs)
    except Exception as exc:
        logger.warning("orchestration_step_write_failed detail=%s", exc)
        return False
    return True


def safe_complete_orchestration_run(run_id: str, **kwargs: Any) -> bool:
    try:
        complete_orchestration_run(run_id, **kwargs)
    except Exception as exc:
        logger.warning("orchestration_run_complete_failed detail=%s", exc)
        return False
    return True


def _run_row(row: Any) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "orchestration_id": row["orchestration_id"],
        "kind": row["kind"],
        "preview_id": row["preview_id"] or "",
        "digest": row["digest"] or "",
        "client_id": row["client_id"] or "",
        "status": row["status"],
        "summary": row["summary"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "approval_consumed": bool(row["approval_consumed"]),
        "definition_domain": row["definition_domain"] or "",
        "definition_version": row["definition_version"] or "",
        "correlation_key": row["correlation_key"] or "",
        "activation_idempotency_key": row["activation_idempotency_key"] or "",
        "controller_version": row["controller_version"] or "",
        "controller_state": _payload(row["controller_state_json"]),
        "cancellation_reason": row["cancellation_reason"] or "",
        "cancellation_requester": row["cancellation_requester"] or "",
        "payload": _payload(row["payload_json"]),
    }


def _step_row(row: Any) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "step_id": row["step_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "ordinal": row["ordinal"],
        "target_type": row["target_type"] or "",
        "target_id": row["target_id"] or "",
        "target_label": row["target_label"] or "",
        "action_id": row["action_id"] or "",
        "policy_id": row["policy_id"] or "",
        "status": row["status"],
        "summary": row["summary"],
        "request_id": row["request_id"] or "",
        "error_class": row["error_class"] or "",
        "verification_status": row["verification_status"] or "",
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "payload": _payload(row["payload_json"]),
    }


def _payload(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
