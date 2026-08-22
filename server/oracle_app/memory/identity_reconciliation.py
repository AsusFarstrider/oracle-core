from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .schema import ensure_schema
from .sources import INTERNAL_SOURCE_DEFINITIONS
from .store import DB_PATH, transaction


_LEGACY_SATELLITE_IDS = (
    (re.compile(r"^pi_satellite_(\d+)$"), r"pi-satellite-\1"),
    (re.compile(r"^server_satellite_(\d+)$"), r"server-satellite-\1"),
    (re.compile(r"^surface_satellite_(\d+)$"), r"surface-\1-satellite"),
)


@dataclass(frozen=True)
class IdentityReconciliationReport:
    users_active: int
    users_disabled: int
    users_retired: int
    sources_active: int
    sources_disabled: int
    sources_retired: int
    aliases_rewritten: tuple[tuple[str, str], ...]


def reconcile_identities(
    household: Any,
    satellites: Any,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> IdentityReconciliationReport:
    """Reconcile historical dimensions from canonical configuration authority.

    Existing unknown identities are retained as retired dimensions. Only aliases
    derived unambiguously from a configured satellite-to-source binding are
    rewritten.
    """
    path = db_path or DB_PATH
    ensure_schema(path)
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    configured_users = dict(getattr(household, "users", {}) or {})
    configured_sources = dict(getattr(household, "sources", {}) or {})
    internal = {str(item["source_id"]): item for item in INTERNAL_SOURCE_DEFINITIONS}
    alias_map = _configured_alias_map(satellites)

    with transaction(path) as conn:
        _rewrite_aliases(conn, alias_map)
        for user_id, user in configured_users.items():
            enabled = bool(getattr(user, "enabled", False))
            payload = {"canonical_identity": True}
            aliases = list(getattr(user, "aliases", ()) or ())
            if aliases:
                payload["aliases"] = aliases
            conn.execute(
                """INSERT INTO memory_users (
                       user_id, created_at, updated_at, display_name, status, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       updated_at=excluded.updated_at, display_name=excluded.display_name,
                       status=excluded.status, payload_json=excluded.payload_json""",
                (
                    str(user_id), observed_at, observed_at,
                    str(getattr(user, "display_name", user_id)),
                    "active" if enabled else "disabled",
                    json.dumps(payload, sort_keys=True),
                ),
            )
        if configured_users:
            placeholders = ",".join("?" for _ in configured_users)
            conn.execute(
                f"UPDATE memory_users SET status='retired', updated_at=? "
                f"WHERE user_id NOT IN ({placeholders})",
                (observed_at, *configured_users),
            )

        authoritative_source_ids = set(internal) | set(configured_sources)
        for source_id, definition in internal.items():
            _upsert_source_dimension(
                conn, source_id, definition["source_type"], definition["display_name"],
                "active", dict(definition.get("payload") or {}), observed_at,
            )
        for source_id, source in configured_sources.items():
            enabled = bool(getattr(source, "enabled", False))
            source_kind = str(getattr(source, "type", ""))
            payload: dict[str, Any] = {
                "canonical_source": True,
                "fixed": bool(getattr(source, "fixed", False)),
                "source_kind": source_kind,
            }
            for field in ("associated_room_id", "associated_user_id"):
                value = getattr(source, field, None)
                if value is not None:
                    payload[field] = value
            _upsert_source_dimension(
                conn,
                str(source_id),
                "satellite" if source_kind == "satellite" else "ui",
                str(source_id),
                "active" if enabled else "disabled",
                payload,
                observed_at,
            )
        if authoritative_source_ids:
            placeholders = ",".join("?" for _ in authoritative_source_ids)
            retiring_active_alerts = conn.execute(
                f"""SELECT alert_id, source_id FROM memory_alerts
                    WHERE status IN ('pending','leased')
                      AND source_id NOT IN ({placeholders})
                    ORDER BY alert_id""",
                tuple(sorted(authoritative_source_ids)),
            ).fetchall()
            if retiring_active_alerts:
                references = ", ".join(
                    f"{row['alert_id']}:{row['source_id']}"
                    for row in retiring_active_alerts
                )
                raise ValueError(
                    "Cannot retire sources with active durable alerts: " + references
                )
            conn.execute(
                f"UPDATE memory_sources SET status='retired', updated_at=? "
                f"WHERE source_id NOT IN ({placeholders})",
                (observed_at, *sorted(authoritative_source_ids)),
            )

        counts = {
            ("users", status): int(conn.execute(
                "SELECT COUNT(*) FROM memory_users WHERE status=?", (status,)
            ).fetchone()[0])
            for status in ("active", "disabled", "retired")
        }
        counts.update({
            ("sources", status): int(conn.execute(
                "SELECT COUNT(*) FROM memory_sources WHERE status=?", (status,)
            ).fetchone()[0])
            for status in ("active", "disabled", "retired")
        })
    return IdentityReconciliationReport(
        users_active=counts[("users", "active")],
        users_disabled=counts[("users", "disabled")],
        users_retired=counts[("users", "retired")],
        sources_active=counts[("sources", "active")],
        sources_disabled=counts[("sources", "disabled")],
        sources_retired=counts[("sources", "retired")],
        aliases_rewritten=tuple(sorted(alias_map.items())),
    )


def _configured_alias_map(satellites: Any) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for satellite_id, satellite in dict(getattr(satellites, "satellites", {}) or {}).items():
        source_id = str(getattr(satellite, "source_id", "") or "")
        if not source_id:
            continue
        for pattern, replacement in _LEGACY_SATELLITE_IDS:
            if pattern.fullmatch(str(satellite_id)):
                aliases[pattern.sub(replacement, str(satellite_id))] = source_id
                break
    return aliases


def _rewrite_aliases(conn: Any, aliases: Mapping[str, str]) -> None:
    for alias, canonical in sorted(aliases.items()):
        if alias == canonical:
            continue
        alias_row = conn.execute(
            "SELECT 1 FROM memory_sources WHERE source_id=?", (alias,)
        ).fetchone()
        if alias_row is None:
            continue
        canonical_row = conn.execute(
            "SELECT 1 FROM memory_sources WHERE source_id=?", (canonical,)
        ).fetchone()
        if canonical_row is None:
            # Canonical configuration will seed this row below; a temporary
            # dimension is needed so foreign-key validity is never ambiguous.
            conn.execute(
                """INSERT INTO memory_sources
                   SELECT ?, created_at, updated_at, source_type, ?, 'active', payload_json
                   FROM memory_sources WHERE source_id=?""",
                (canonical, canonical, alias),
            )
        for table in (
            "memory_events",
            "memory_sessions",
            "memory_transcripts",
            "memory_alerts",
            "memory_alert_transitions",
        ):
            conn.execute(f"UPDATE {table} SET source_id=? WHERE source_id=?", (canonical, alias))
        _merge_projection_alias(conn, alias, canonical)
        conn.execute("DELETE FROM memory_sources WHERE source_id=?", (alias,))


def _merge_projection_alias(conn: Any, alias: str, canonical: str) -> None:
    rows = conn.execute(
        "SELECT * FROM memory_current_projections WHERE source_id=?", (alias,)
    ).fetchall()
    for row in rows:
        old_id = str(row["projection_id"])
        new_id = old_id[:-len(alias)] + canonical if old_id.endswith(alias) else old_id
        existing = conn.execute(
            "SELECT * FROM memory_current_projections WHERE projection_id=?", (new_id,)
        ).fetchone()
        if existing is not None:
            keep_alias = str(row["observed_at"]) > str(existing["observed_at"])
            if keep_alias:
                conn.execute("DELETE FROM memory_current_projections WHERE projection_id=?", (new_id,))
            else:
                conn.execute("DELETE FROM memory_current_projections WHERE projection_id=?", (old_id,))
                continue
        conn.execute(
            """UPDATE memory_current_projections
               SET projection_id=?, source_id=?, provider=CASE WHEN provider=? THEN ? ELSE provider END
               WHERE projection_id=?""",
            (new_id, canonical, alias, canonical, old_id),
        )


def _upsert_source_dimension(
    conn: Any,
    source_id: str,
    source_type: str,
    display_name: str,
    status: str,
    payload: dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO memory_sources (
               source_id, created_at, updated_at, source_type, display_name, status, payload_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
               updated_at=excluded.updated_at, source_type=excluded.source_type,
               display_name=excluded.display_name, status=excluded.status,
               payload_json=excluded.payload_json""",
        (source_id, now, now, source_type, display_name, status, json.dumps(payload, sort_keys=True)),
    )
