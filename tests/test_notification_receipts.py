from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from oracle_app.notifications.receipts import (
    NotificationDeliveryQuery,
    get_notification_delivery,
    list_due_notification_deliveries,
    list_notification_deliveries,
    reserve_notification_delivery,
    summarize_notification_deliveries,
    transition_notification_delivery,
)


class NotificationDeliveryReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.now = datetime(2026, 6, 28, 20, 0, tzinfo=UTC)

    def _reserve(
        self,
        *,
        occurrence_id: str = "run-1:1",
        repeat_policy: str = "first_per_correlation",
    ):
        return reserve_notification_delivery(
            notification_type="side_entry_open",
            occurrence_id=occurrence_id,
            correlation_id="run-1",
            channel="external",
            destination_id="primary",
            provider="apprise",
            max_attempts=3,
            retry_seconds=30,
            expires_at=(self.now + timedelta(minutes=5)).isoformat(),
            failure_policy="best_effort",
            repeat_policy=repeat_policy,
            db_path=self.db_path,
        )

    def test_reservation_is_durable_and_idempotent(self) -> None:
        first, first_created = self._reserve()
        second, second_created = self._reserve()

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["attempt_count"], 0)
        self.assertEqual(
            get_notification_delivery(first["receipt_id"], db_path=self.db_path),
            second,
        )

    def test_first_per_correlation_reuses_first_occurrence_receipt(self) -> None:
        first, first_created = self._reserve(occurrence_id="run-1:1")
        repeated, repeated_created = self._reserve(occurrence_id="run-1:2")

        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["receipt_id"], first["receipt_id"])
        self.assertEqual(repeated["occurrence_id"], "run-1:1")

    def test_every_occurrence_reserves_distinct_receipts(self) -> None:
        first, first_created = self._reserve(
            occurrence_id="run-1:1",
            repeat_policy="every_occurrence",
        )
        repeated, repeated_created = self._reserve(
            occurrence_id="run-1:2",
            repeat_policy="every_occurrence",
        )

        self.assertTrue(first_created)
        self.assertTrue(repeated_created)
        self.assertNotEqual(repeated["receipt_id"], first["receipt_id"])

    def test_summary_counts_all_receipt_states_for_channel(self) -> None:
        pending, _created = self._reserve(occurrence_id="run-1:1")
        accepted, _created = self._reserve(
            occurrence_id="run-2:1",
            repeat_policy="every_occurrence",
        )
        transition_notification_delivery(
            accepted["receipt_id"],
            status="accepted",
            attempted=True,
            db_path=self.db_path,
        )

        summary = summarize_notification_deliveries(
            channel="external",
            db_path=self.db_path,
        )

        self.assertEqual(summary, {"total": 2, "by_status": {"accepted": 1, "pending": 1}})
        self.assertEqual(pending["status"], "pending")

    def test_retry_then_accept_records_attempts_and_terminal_state(self) -> None:
        receipt, _created = self._reserve()
        retry_at = (self.now + timedelta(seconds=30)).isoformat()

        waiting = transition_notification_delivery(
            receipt["receipt_id"],
            status="retry_wait",
            attempted=True,
            next_attempt_at=retry_at,
            last_error_class="TimeoutError",
            last_error_code="provider_timeout",
            db_path=self.db_path,
        )
        accepted = transition_notification_delivery(
            receipt["receipt_id"],
            status="accepted",
            attempted=True,
            db_path=self.db_path,
        )

        self.assertEqual(waiting["attempt_count"], 1)
        self.assertEqual(waiting["next_attempt_at"], retry_at)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["attempt_count"], 2)
        self.assertIsNotNone(accepted["accepted_at"])
        self.assertIsNotNone(accepted["completed_at"])
        self.assertIsNone(accepted["next_attempt_at"])

    def test_terminal_receipt_rejects_later_transition(self) -> None:
        receipt, _created = self._reserve()
        transition_notification_delivery(
            receipt["receipt_id"],
            status="suppressed",
            db_path=self.db_path,
        )

        with self.assertRaises(ValueError):
            transition_notification_delivery(
                receipt["receipt_id"],
                status="accepted",
                attempted=True,
                db_path=self.db_path,
            )

    def test_retry_wait_requires_due_time_and_honors_attempt_bound(self) -> None:
        receipt, _created = self._reserve()

        with self.assertRaises(ValueError):
            transition_notification_delivery(
                receipt["receipt_id"],
                status="retry_wait",
                attempted=True,
                db_path=self.db_path,
            )

        for index in range(3):
            if index:
                transition_notification_delivery(
                    receipt["receipt_id"],
                    status="pending",
                    db_path=self.db_path,
                )
            transition_notification_delivery(
                receipt["receipt_id"],
                status="retry_wait",
                attempted=True,
                next_attempt_at=(self.now + timedelta(seconds=30 + index)).isoformat(),
                db_path=self.db_path,
            )
        transition_notification_delivery(
            receipt["receipt_id"],
            status="pending",
            db_path=self.db_path,
        )

        with self.assertRaises(ValueError):
            transition_notification_delivery(
                receipt["receipt_id"],
                status="failed",
                attempted=True,
                db_path=self.db_path,
            )

    def test_due_and_filtered_queries_use_sanitized_identity_fields(self) -> None:
        due, _created = self._reserve(occurrence_id="run-1:1")
        future, _created = self._reserve(
            occurrence_id="run-1:2",
            repeat_policy="every_occurrence",
        )
        transition_notification_delivery(
            future["receipt_id"],
            status="retry_wait",
            next_attempt_at=(self.now + timedelta(minutes=1)).isoformat(),
            db_path=self.db_path,
        )

        due_rows = list_due_notification_deliveries(
            now=self.now.isoformat(),
            db_path=self.db_path,
        )
        filtered = list_notification_deliveries(
            NotificationDeliveryQuery(status="retry_wait", provider="apprise"),
            db_path=self.db_path,
        )

        self.assertEqual([item["receipt_id"] for item in due_rows], [due["receipt_id"]])
        self.assertEqual([item["receipt_id"] for item in filtered], [future["receipt_id"]])


if __name__ == "__main__":
    unittest.main()
