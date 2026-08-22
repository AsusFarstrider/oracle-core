from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from oracle_app.notifications.external_worker import process_due_external_deliveries
from oracle_app.notifications.receipts import (
    get_notification_delivery,
    reserve_notification_delivery,
)
from oracle_app.provider_bridges.apprise import (
    AppriseBridgeHttpError,
    AppriseBridgeUnreachableError,
)


class _FakeBridge:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [{"status": "accepted"}])
        self.calls: list[dict[str, object]] = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ExternalNotificationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.now = datetime(2026, 6, 28, 20, 0, tzinfo=UTC)
        self.notification_settings = {
            "recipient_groups": {
                "primary": {
                    "id": "primary",
                    "enabled": True,
                    "provider": "apprise",
                    "config_key": "oracle",
                    "routing_tag": "primary",
                }
            },
            "notifications": {
                "side_entry_open": {
                    "id": "side_entry_open",
                    "enabled": True,
                    "message": "The side entry has been open for more than ten minutes.",
                    "external_delivery": {
                        "enabled": True,
                        "recipient_groups": ["primary"],
                    },
                }
            },
        }
        self.apprise_settings = {
            "enabled": True,
            "base_url": "http://127.0.0.1:8020",
            "timeout_seconds": 8,
        }

    def _reserve(
        self,
        *,
        occurrence_id: str = "run-1:1",
        channel: str = "external",
        max_attempts: int = 3,
        expires_at: datetime | None = None,
    ) -> dict[str, object]:
        receipt, _created = reserve_notification_delivery(
            notification_type="side_entry_open",
            occurrence_id=occurrence_id,
            correlation_id="run-1",
            channel=channel,
            destination_id="primary",
            provider="apprise",
            max_attempts=max_attempts,
            retry_seconds=30,
            expires_at=(expires_at or self.now + timedelta(minutes=5)).isoformat(),
            failure_policy="best_effort",
            repeat_policy="first_per_correlation",
            db_path=self.db_path,
        )
        return receipt

    def _process(self, bridge: _FakeBridge, *, now: datetime | None = None):
        return process_due_external_deliveries(
            now=now or self.now,
            db_path=self.db_path,
            notification_settings=self.notification_settings,
            apprise_settings=self.apprise_settings,
            bridge=bridge,
            suppression_evaluator=lambda *_args, **_kwargs: "inactive",
        )

    def test_accepted_delivery_records_attempt_and_logical_route(self) -> None:
        receipt = self._reserve()
        bridge = _FakeBridge()

        outcomes = self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes[0]["status"], "accepted")
        self.assertEqual(stored["attempt_count"], 1)
        self.assertEqual(bridge.calls[0]["config_key"], "oracle")
        self.assertEqual(bridge.calls[0]["routing_tag"], "primary")
        self.assertEqual(
            bridge.calls[0]["body"],
            "The side entry has been open for more than ten minutes.",
        )

    def test_retryable_failure_waits_then_retries(self) -> None:
        receipt = self._reserve()
        unavailable = AppriseBridgeUnreachableError("Apprise is unreachable.")
        bridge = _FakeBridge([unavailable, {"status": "accepted"}])

        first = self._process(bridge)
        before_due = self._process(bridge, now=self.now + timedelta(seconds=29))
        second = self._process(bridge, now=self.now + timedelta(seconds=30))
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(first[0]["status"], "retry_wait")
        self.assertEqual(before_due, [])
        self.assertEqual(second[0]["status"], "accepted")
        self.assertEqual(stored["attempt_count"], 2)
        self.assertEqual(len(bridge.calls), 2)

    def test_retryable_failure_at_attempt_limit_is_terminal(self) -> None:
        receipt = self._reserve(max_attempts=1)
        bridge = _FakeBridge([AppriseBridgeUnreachableError("offline")])

        outcomes = self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes[0]["status"], "failed")
        self.assertEqual(stored["attempt_count"], 1)
        self.assertEqual(stored["last_error_code"], "provider_unavailable")

    def test_permanent_provider_rejection_does_not_retry(self) -> None:
        receipt = self._reserve()
        bridge = _FakeBridge([AppriseBridgeHttpError(status_code=400)])

        self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["last_error_code"], "http_400")

    def test_expired_receipt_is_not_sent(self) -> None:
        receipt = self._reserve(expires_at=self.now - timedelta(seconds=1))
        bridge = _FakeBridge()

        outcomes = self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes[0]["status"], "expired")
        self.assertEqual(stored["attempt_count"], 0)
        self.assertEqual(bridge.calls, [])

    def test_disabled_definition_fails_without_provider_attempt(self) -> None:
        receipt = self._reserve()
        self.notification_settings["notifications"]["side_entry_open"][
            "external_delivery"
        ]["enabled"] = False
        bridge = _FakeBridge()

        self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["attempt_count"], 0)
        self.assertEqual(stored["last_error_code"], "invalid_external_delivery_config")
        self.assertEqual(bridge.calls, [])

    def test_active_suppression_stops_delivery_without_provider_attempt(self) -> None:
        receipt = self._reserve()
        bridge = _FakeBridge()

        outcomes = process_due_external_deliveries(
            now=self.now,
            db_path=self.db_path,
            notification_settings=self.notification_settings,
            apprise_settings=self.apprise_settings,
            bridge=bridge,
            suppression_evaluator=lambda *_args, **_kwargs: "active",
        )
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes[0]["status"], "suppressed")
        self.assertEqual(stored["attempt_count"], 0)
        self.assertEqual(bridge.calls, [])

    def test_unavailable_suppression_defers_without_provider_attempt(self) -> None:
        receipt = self._reserve()
        bridge = _FakeBridge()

        outcomes = process_due_external_deliveries(
            now=self.now,
            db_path=self.db_path,
            notification_settings=self.notification_settings,
            apprise_settings=self.apprise_settings,
            bridge=bridge,
            suppression_evaluator=lambda *_args, **_kwargs: "unavailable",
        )
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes[0]["status"], "retry_wait")
        self.assertEqual(stored["attempt_count"], 0)
        self.assertEqual(stored["last_error_code"], "suppression_unavailable")
        self.assertEqual(bridge.calls, [])

    def test_non_external_receipts_are_ignored(self) -> None:
        receipt = self._reserve(channel="satellite")
        bridge = _FakeBridge()

        outcomes = self._process(bridge)
        stored = get_notification_delivery(str(receipt["receipt_id"]), db_path=self.db_path)

        self.assertEqual(outcomes, [])
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(bridge.calls, [])


if __name__ == "__main__":
    unittest.main()
