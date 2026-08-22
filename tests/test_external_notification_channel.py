from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from oracle_app.notifications.channels.external import reserve_external_deliveries
from oracle_app.notifications.receipts import get_notification_delivery


class ExternalNotificationChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.now = datetime(2026, 6, 28, 21, 0, tzinfo=UTC)
        self.settings = {
            "recipient_groups": {
                "primary": {
                    "enabled": True,
                    "provider": "apprise",
                }
            }
        }
        self.definition = {
            "external_delivery": {
                "enabled": True,
                "recipient_groups": ["primary"],
                "delivery_ttl_seconds": 300,
                "max_attempts": 3,
                "retry_seconds": 30,
                "repeat_policy": "first_per_correlation",
                "failure_policy": "best_effort",
            }
        }

    def _reserve(self, occurrence_id: str):
        return reserve_external_deliveries(
            notification_type="side_entry_open",
            occurrence_id=occurrence_id,
            correlation_id="run-1",
            definition=self.definition,
            settings=self.settings,
            now=self.now,
            db_path=self.db_path,
        )

    def test_reserves_frozen_external_work(self) -> None:
        result = self._reserve("run-1:1")
        receipt = get_notification_delivery(result["receipt_ids"][0], db_path=self.db_path)

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["queued_count"], 1)
        self.assertEqual(receipt["channel"], "external")
        self.assertEqual(receipt["destination_id"], "primary")
        self.assertEqual(receipt["retry_seconds"], 30)
        self.assertEqual(receipt["status"], "pending")

    def test_first_per_correlation_reports_repeat_as_duplicate(self) -> None:
        first = self._reserve("run-1:1")
        repeated = self._reserve("run-1:2")

        self.assertEqual(first["status"], "queued")
        self.assertEqual(repeated["status"], "duplicate")
        self.assertEqual(repeated["receipt_ids"], first["receipt_ids"])

    def test_first_per_correlation_requires_run_identity(self) -> None:
        with self.assertRaises(ValueError):
            reserve_external_deliveries(
                notification_type="side_entry_open",
                occurrence_id="event-1",
                correlation_id="",
                definition=self.definition,
                settings=self.settings,
                now=self.now,
                db_path=self.db_path,
            )


if __name__ == "__main__":
    unittest.main()
