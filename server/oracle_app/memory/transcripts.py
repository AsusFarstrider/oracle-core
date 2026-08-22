from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .correlation import get_correlation_id
from .retention import RetentionPolicy
from .schema import ensure_schema
from .store import DB_PATH, transaction


logger = logging.getLogger("oracle-brain.memory.transcripts")


@dataclass(frozen=True)
class TranscriptQuery:
    transcript_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    source_id: str | None = None
    user_id: str | None = None
    fallback_used: bool | None = None
    final_domain: str | None = None
    final_intent: str | None = None
    final_status: str | None = None
    failure_stage: str | None = None
    captured_after: str | None = None
    captured_before: str | None = None
    include_raw_transcript: bool = False
    limit: int = 100
    offset: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_record_transcript(**kwargs: Any) -> bool:
    try:
        if not kwargs.get("correlation_id"):
            kwargs["correlation_id"] = get_correlation_id()
        record_transcript(**kwargs)
    except Exception as exc:
        logger.warning("memory_transcript_write_failed detail=%s", exc)
        return False
    return True


def safe_enrich_transcripts_for_correlation(correlation_id: str | None, **kwargs: Any) -> int:
    try:
        return enrich_transcripts_for_correlation(correlation_id, **kwargs)
    except Exception as exc:
        logger.warning("memory_transcript_enrichment_failed correlation_id=%s detail=%s", correlation_id or "-", exc)
        return 0


def record_transcript(
    *,
    session_id: str | None = None,
    correlation_id: str | None = None,
    source_id: str | None = None,
    user_id: str | None = None,
    captured_at: str | None = None,
    raw_transcript: str | None = None,
    normalized_text: str | None = None,
    stt_provider: str | None = None,
    stt_model: str | None = None,
    confidence: float | None = None,
    route_result: dict[str, Any] | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    final_domain: str | None = None,
    final_intent: str | None = None,
    final_status: str = "unknown",
    failure_stage: str | None = None,
    payload: dict[str, Any] | None = None,
    transcript_id: str | None = None,
    retention_policy: RetentionPolicy,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = db_path or DB_PATH
    ensure_schema(path)
    now = utc_now_iso()
    resolved_transcript_id = _clean_filter(transcript_id) or uuid.uuid4().hex
    resolved_captured_at = captured_at or now
    clean_final_status = _clean_required(final_status, "final_status")
    resolved_session_id = _existing_reference("memory_sessions", "session_id", session_id, db_path=path)
    resolved_source_id = _existing_reference("memory_sources", "source_id", source_id, db_path=path)
    resolved_user_id = _existing_reference("memory_users", "user_id", user_id, db_path=path)
    raw_retention_until, metadata_retention_until = _retention_until_values(
        captured_at=resolved_captured_at,
        final_status=clean_final_status,
        failure_stage=failure_stage,
        confidence=confidence,
        policy=retention_policy,
    )
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_transcripts (
                transcript_id, created_at, updated_at, session_id, correlation_id,
                source_id, user_id, captured_at, raw_transcript, normalized_text,
                stt_provider, stt_model, confidence, route_result_json,
                fallback_used, fallback_reason, final_domain, final_intent,
                final_status, failure_stage, raw_transcript_retention_until,
                metadata_retention_until, raw_transcript_pruned_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_transcript_id,
                now,
                now,
                resolved_session_id,
                _clean_filter(correlation_id),
                resolved_source_id,
                resolved_user_id,
                resolved_captured_at,
                raw_transcript,
                normalized_text,
                _clean_filter(stt_provider),
                _clean_filter(stt_model),
                confidence,
                json.dumps(route_result or {}, sort_keys=True),
                1 if fallback_used else 0,
                _clean_filter(fallback_reason),
                _clean_filter(final_domain),
                _clean_filter(final_intent),
                clean_final_status,
                _clean_filter(failure_stage),
                raw_retention_until,
                metadata_retention_until,
                None,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )
    transcript = get_transcript(
        resolved_transcript_id,
        db_path=path,
    )
    if transcript is None:
        raise RuntimeError(f"Failed to load Oracle Memory transcript {resolved_transcript_id}")
    return transcript


def enrich_transcripts_for_correlation(
    correlation_id: str | None,
    *,
    session_id: str | None = None,
    source_id: str | None = None,
    user_id: str | None = None,
    normalized_text: str | None = None,
    route_result: dict[str, Any] | None = None,
    fallback_used: bool | None = None,
    fallback_reason: str | None = None,
    final_domain: str | None = None,
    final_intent: str | None = None,
    final_status: str | None = None,
    failure_stage: str | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> int:
    clean_correlation_id = _clean_filter(correlation_id)
    if not clean_correlation_id:
        return 0
    path = db_path or DB_PATH
    ensure_schema(path)
    now = utc_now_iso()
    assignments: list[str] = ["updated_at = ?"]
    args: list[Any] = [now]
    updates = {
        "session_id": _existing_reference("memory_sessions", "session_id", session_id, db_path=path),
        "source_id": _existing_reference("memory_sources", "source_id", source_id, db_path=path),
        "user_id": _existing_reference("memory_users", "user_id", user_id, db_path=path),
        "normalized_text": normalized_text,
        "fallback_reason": _clean_filter(fallback_reason),
        "final_domain": _clean_filter(final_domain),
        "final_intent": _clean_filter(final_intent),
        "final_status": _clean_filter(final_status),
        "failure_stage": _clean_filter(failure_stage),
    }
    for column, value in updates.items():
        if value is not None:
            assignments.append(f"{column} = ?")
            args.append(value)
    if route_result is not None:
        assignments.append("route_result_json = ?")
        args.append(json.dumps(route_result, sort_keys=True))
    if fallback_used is not None:
        assignments.append("fallback_used = ?")
        args.append(1 if fallback_used else 0)
    if payload is not None:
        assignments.append("payload_json = ?")
        args.append(json.dumps(payload, sort_keys=True))
    args.append(clean_correlation_id)
    with transaction(path) as conn:
        cursor = conn.execute(
            f"UPDATE memory_transcripts SET {', '.join(assignments)} WHERE correlation_id = ?",
            args,
        )
        return int(cursor.rowcount or 0)


def get_transcript(
    transcript_id: str,
    *,
    include_raw_transcript: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_transcripts WHERE transcript_id = ?",
            (_clean_required(transcript_id, "transcript_id"),),
        ).fetchone()
    return _row_to_transcript(row, include_raw_transcript=include_raw_transcript) if row else None


def query_transcripts(
    query: TranscriptQuery | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    query = query or TranscriptQuery()
    path = db_path or DB_PATH
    ensure_schema(path)
    sql = "SELECT * FROM memory_transcripts"
    args: list[Any] = []
    where: list[str] = []
    filters = {
        "transcript_id": _clean_filter(query.transcript_id),
        "session_id": _clean_filter(query.session_id),
        "correlation_id": _clean_filter(query.correlation_id),
        "source_id": _clean_filter(query.source_id),
        "user_id": _clean_filter(query.user_id),
        "final_domain": _clean_filter(query.final_domain),
        "final_intent": _clean_filter(query.final_intent),
        "final_status": _clean_filter(query.final_status),
        "failure_stage": _clean_filter(query.failure_stage),
    }
    for column, value in filters.items():
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if query.fallback_used is not None:
        where.append("fallback_used = ?")
        args.append(1 if query.fallback_used else 0)
    captured_after = _clean_filter(query.captured_after)
    if captured_after:
        where.append("captured_at >= ?")
        args.append(captured_after)
    captured_before = _clean_filter(query.captured_before)
    if captured_before:
        where.append("captured_at <= ?")
        args.append(captured_before)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY captured_at DESC, transcript_id DESC LIMIT ? OFFSET ?"
    args.extend([_clamp_limit(query.limit), _clean_offset(query.offset)])
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        _row_to_transcript(row, include_raw_transcript=query.include_raw_transcript)
        for row in rows
    ]


def recent_transcripts(
    *,
    limit: int = 100,
    include_raw_transcript: bool = False,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    return query_transcripts(
        TranscriptQuery(limit=limit, include_raw_transcript=include_raw_transcript),
        db_path=db_path,
    )


def _row_to_transcript(row: Any, *, include_raw_transcript: bool) -> dict[str, Any]:
    transcript = {
        "transcript_id": row["transcript_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "session_id": row["session_id"],
        "correlation_id": row["correlation_id"],
        "source_id": row["source_id"],
        "user_id": row["user_id"],
        "captured_at": row["captured_at"],
        "normalized_text": row["normalized_text"],
        "stt_provider": row["stt_provider"],
        "stt_model": row["stt_model"],
        "confidence": row["confidence"],
        "route_result": _parse_route_result_json(row["route_result_json"]),
        "fallback_used": bool(row["fallback_used"]),
        "fallback_reason": row["fallback_reason"],
        "final_domain": row["final_domain"],
        "final_intent": row["final_intent"],
        "final_status": row["final_status"],
        "failure_stage": row["failure_stage"],
        "raw_transcript_retention_until": row["raw_transcript_retention_until"],
        "metadata_retention_until": row["metadata_retention_until"],
        "raw_transcript_pruned_at": row["raw_transcript_pruned_at"],
        "payload": _parse_payload_json(row["payload_json"]),
    }
    if include_raw_transcript:
        transcript["raw_transcript"] = row["raw_transcript"]
    return transcript


def _retention_until_values(
    *,
    captured_at: str,
    final_status: str,
    failure_stage: str | None,
    confidence: float | None,
    policy: RetentionPolicy,
) -> tuple[str | None, str | None]:
    captured = _parse_datetime(captured_at)
    if captured is None:
        return None, None
    raw_days = (
        policy.failed_raw_transcript_days
        if _uses_failed_raw_retention(final_status=final_status, failure_stage=failure_stage, confidence=confidence)
        else policy.successful_raw_transcript_days
    )
    return (
        (captured + timedelta(days=raw_days)).isoformat(),
        (captured + timedelta(days=policy.transcript_metadata_days)).isoformat(),
    )


def _uses_failed_raw_retention(*, final_status: str, failure_stage: str | None, confidence: float | None) -> bool:
    status = str(final_status or "").strip().lower()
    if failure_stage:
        return True
    if status in {"failed", "failure", "error", "rejected", "low_confidence"}:
        return True
    if confidence is not None and confidence < 0.5:
        return True
    return False


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_required(value: str | None, field_name: str) -> str:
    cleaned = _clean_filter(value)
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _clean_filter(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _clean_offset(offset: int) -> int:
    try:
        return max(0, int(offset or 0))
    except (TypeError, ValueError):
        return 0


def _existing_reference(table: str, column: str, value: str | None, *, db_path: Path) -> str | None:
    cleaned = _clean_filter(value)
    if not cleaned:
        return None
    with transaction(db_path) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?",
            (cleaned,),
        ).fetchone()
    return cleaned if row else None


def _parse_payload_json(value: str | None) -> dict[str, Any]:
    raw = value or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    if not isinstance(parsed, dict):
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    return parsed


def _parse_route_result_json(value: str | None) -> dict[str, Any]:
    raw = value or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_route_result_parse_error": True, "raw_route_result_json": raw}
    if not isinstance(parsed, dict):
        return {"_route_result_parse_error": True, "raw_route_result_json": raw}
    return parsed
