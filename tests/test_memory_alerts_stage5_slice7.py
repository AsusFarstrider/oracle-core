from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration
from oracle_app.memory.alerts import (
    acknowledge_alert,
    claim_due_alerts,
    create_alert_record,
    import_legacy_alerts,
    list_alert_records,
)
from oracle_app.memory.identity_reconciliation import reconcile_identities
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.memory.retention_executor import run_retention
from oracle_app.memory.sources import seed_sources
from oracle_app.memory.store import transaction
from oracle_app.notifications.receipts import (
    NotificationDeliveryQuery,
    list_notification_deliveries,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


class Stage5Slice7MemoryAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "memory.sqlite3"
        seed_sources(
            [
                {
                    "source_id": source_id,
                    "source_type": "satellite",
                    "display_name": source_id,
                }
                for source_id in ("source-a", "source-b")
            ],
            db_path=self.db_path,
        )

    def _create(self, **overrides):
        values = {
            "kind": "timer",
            "due_at": NOW,
            "message": "Timer finished.",
            "source_id": "source-a",
            "session_id": "session-a",
            "created_at": NOW - timedelta(minutes=1),
            "db_path": self.db_path,
        }
        values.update(overrides)
        return create_alert_record(**values)

    def test_claim_is_leased_retry_safe_and_acknowledgement_is_idempotent(self) -> None:
        alert, created = self._create(idempotency_key="same")
        duplicate, duplicate_created = self._create(idempotency_key="same")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.alert_id, alert.alert_id)

        first = claim_due_alerts(
            source_id="source-a", now=NOW, lease_seconds=30, db_path=self.db_path
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(claim_due_alerts(
            source_id="source-a", now=NOW, lease_seconds=30, db_path=self.db_path
        ), [])
        retried = claim_due_alerts(
            source_id="source-a",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
            db_path=self.db_path,
        )
        self.assertEqual([item.alert_id for item in retried], [alert.alert_id])
        self.assertNotEqual(first[0].lease_id, retried[0].lease_id)

        acknowledged = acknowledge_alert(
            alert_id=alert.alert_id,
            source_id="source-a",
            lease_id=str(retried[0].lease_id),
            now=NOW + timedelta(seconds=32),
            db_path=self.db_path,
        )
        self.assertEqual(acknowledged.status, "acknowledged")
        self.assertEqual(
            acknowledge_alert(
                alert_id=alert.alert_id,
                source_id="source-a",
                lease_id=str(retried[0].lease_id),
                now=NOW + timedelta(seconds=33),
                db_path=self.db_path,
            ).status,
            "acknowledged",
        )
        with self.assertRaisesRegex(ValueError, "lease mismatch"):
            acknowledge_alert(
                alert_id=alert.alert_id,
                source_id="source-a",
                lease_id="lease-wrong",
                now=NOW + timedelta(seconds=33),
                db_path=self.db_path,
            )

    def test_migration_imports_canonical_records_and_rejects_invalid_inventory(self) -> None:
        base = {
            "kind": "reminder",
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            "due_at": NOW.isoformat(),
            "message": "Remember.",
            "metadata": {},
        }
        report = import_legacy_alerts(
            [
                {**base, "alert_id": "valid", "source": "source-a"},
                {**base, "alert_id": "ephemeral", "source": "ephemeral_http"},
                {**base, "alert_id": "bad-kind", "source": "source-a", "kind": "unknown"},
            ],
            db_path=self.db_path,
        )
        self.assertEqual(report, {"imported": 1, "duplicates": 0, "rejected": 2})
        retried = import_legacy_alerts(
            [{**base, "alert_id": "valid", "source": "source-a"}],
            db_path=self.db_path,
        )
        self.assertEqual(retried, {"imported": 0, "duplicates": 1, "rejected": 0})

        notification_report = import_legacy_alerts(
            [{
                **base,
                "alert_id": "notification",
                "source": "source-a",
                "kind": "notification",
                "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
                "delivered": True,
                "metadata": {"notification_id": "door", "event_id": "legacy-event"},
            }],
            db_path=self.db_path,
        )
        self.assertEqual(
            notification_report,
            {"imported": 1, "duplicates": 0, "rejected": 0},
        )
        [receipt] = list_notification_deliveries(
            NotificationDeliveryQuery(
                notification_type="door", occurrence_id="legacy-event"
            ),
            db_path=self.db_path,
        )
        self.assertEqual(receipt["status"], "accepted")

    def test_retention_protects_active_and_deletes_terminal_with_transitions(self) -> None:
        active, _ = self._create(alert_id="active")
        terminal, _ = self._create(alert_id="terminal")
        claimed = claim_due_alerts(
            source_id="source-a", now=NOW, lease_seconds=30, limit=10, db_path=self.db_path
        )
        terminal_lease = next(item for item in claimed if item.alert_id == terminal.alert_id)
        acknowledge_alert(
            alert_id=terminal.alert_id,
            source_id="source-a",
            lease_id=str(terminal_lease.lease_id),
            now=NOW + timedelta(seconds=1),
            completed=True,
            db_path=self.db_path,
        )
        with transaction(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_alerts SET updated_at=?, completed_at=? WHERE alert_id='terminal'",
                ((NOW - timedelta(days=91)).isoformat(),) * 2,
            )
            conn.execute(
                "UPDATE memory_alerts SET status='pending', lease_id=NULL, leased_at=NULL, lease_expires_at=NULL WHERE alert_id='active'"
            )
        policy = retention_policy_from_configuration(MemoryRetentionConfiguration())
        dry_run = run_retention(policy, db_path=self.db_path, now=NOW, dry_run=True)
        report = {item.class_name: item for item in dry_run.classes}["terminal_alerts"]
        self.assertEqual(report.candidate_ids, ("terminal",))
        self.assertEqual(report.protected_ids, ("active",))

        run_retention(policy, db_path=self.db_path, now=NOW, dry_run=False)
        self.assertEqual(
            [item.alert_id for item in list_alert_records(db_path=self.db_path)],
            [active.alert_id],
        )
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM memory_alert_transitions WHERE alert_id='terminal'"
            ).fetchone()[0], 0)

    def test_identity_reconciliation_blocks_retirement_with_active_alert(self) -> None:
        self._create(source_id="source-b")
        household = SimpleNamespace(users={}, sources={
            "source-a": SimpleNamespace(enabled=True, type="satellite", fixed=True),
        })
        satellites = SimpleNamespace(satellites={})
        with self.assertRaisesRegex(ValueError, "active durable alerts"):
            reconcile_identities(household, satellites, db_path=self.db_path)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM memory_sources WHERE source_id='source-b'"
            ).fetchone()[0], "active")


if __name__ == "__main__":
    unittest.main()
