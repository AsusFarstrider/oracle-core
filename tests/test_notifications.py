from __future__ import annotations

from datetime import datetime, timedelta

from alert_store_test_support import IsolatedAlertStoreTestCase
from oracle_app.alerts import consume_due_alerts, create_alert_batch, list_due_alerts
from oracle_app.memory.alerts import acknowledge_alert, claim_due_alerts
from oracle_app.notifications.channels.satellite_announcement import (
    dispatch_satellite_announcement_values,
    reconcile_satellite_receipts,
    satellite_alert_claim_needs_work,
)
from oracle_app.notifications.receipts import (
    NotificationDeliveryQuery,
    list_notification_deliveries,
)


class NotificationAlertStoreTests(IsolatedAlertStoreTestCase):
    def test_empty_claim_preflight_detects_only_owned_durable_work(self) -> None:
        now = datetime.now().astimezone()
        self.assertFalse(
            satellite_alert_claim_needs_work(
                "source-a", now=now, db_path=self.alert_db_path
            )
        )
        create_alert_batch(
            kind="timer",
            due_at=now + timedelta(minutes=1),
            message="Later.",
            sources=["source-a"],
            session_id=None,
        )
        self.assertFalse(
            satellite_alert_claim_needs_work(
                "source-a", now=now, db_path=self.alert_db_path
            )
        )
        self.assertTrue(
            satellite_alert_claim_needs_work(
                "source-a", now=now + timedelta(minutes=1), db_path=self.alert_db_path
            )
        )

    def test_claim_preflight_keeps_future_notification_receipt_repair(self) -> None:
        now = datetime.now().astimezone()
        create_alert_batch(
            kind="notification",
            due_at=now + timedelta(minutes=1),
            message="Later.",
            sources=["source-a"],
            session_id=None,
            metadata={"notification_id": "door_open", "event_id": "event-1"},
            expires_at=now + timedelta(minutes=2),
        )

        self.assertTrue(
            satellite_alert_claim_needs_work(
                "source-a", now=now, db_path=self.alert_db_path
            )
        )

    def test_satellite_receipt_tracks_acknowledgement_suppression_and_expiry(self) -> None:
        now = datetime.now().astimezone()
        for occurrence_id in ("accepted", "suppressed", "expired"):
            dispatch_satellite_announcement_values(
                notification_type="door_open",
                occurrence_id=occurrence_id,
                targets=("source-a",),
                message="Door open.",
                audio_policy="pause_resume",
                delivery_ttl_seconds=1,
                caller="test",
                now=now,
            )

        accepted = claim_due_alerts(
            source_id="source-a",
            now=now,
            notification_decisions={
                list_due_alerts("source-a", kind="notification")[0].alert_id: "deliver"
            },
            db_path=self.alert_db_path,
        )
        self.assertEqual(len(accepted), 1)
        acknowledge_alert(
            alert_id=accepted[0].alert_id,
            source_id="source-a",
            lease_id=str(accepted[0].lease_id),
            now=now,
            db_path=self.alert_db_path,
        )
        remaining = list_due_alerts("source-a", kind="notification")
        suppressed_id = next(
            item.alert_id for item in remaining if item.metadata["event_id"] == "suppressed"
        )
        claim_due_alerts(
            source_id="source-a",
            now=now,
            notification_decisions={suppressed_id: "suppress"},
            db_path=self.alert_db_path,
        )
        claim_due_alerts(
            source_id="source-a",
            now=now + timedelta(seconds=2),
            notification_decisions={},
            db_path=self.alert_db_path,
        )
        reconcile_satellite_receipts("source-a")
        receipts = list_notification_deliveries(
            NotificationDeliveryQuery(notification_type="door_open", limit=10),
            db_path=self.alert_db_path,
        )
        self.assertEqual(
            {item["occurrence_id"]: item["status"] for item in receipts},
            {"accepted": "accepted", "suppressed": "suppressed", "expired": "expired"},
        )

    def test_batch_fanout_is_source_scoped_and_idempotent(self) -> None:
        now = datetime.now().astimezone()
        created, duplicate = create_alert_batch(
            kind="notification",
            due_at=now,
            message="Door open.",
            sources=["source-a", "source-b"],
            session_id=None,
            metadata={"notification_id": "door_open", "event_id": "event-1"},
            expires_at=now + timedelta(seconds=90),
            idempotency_key="notification:door_open:event-1",
        )
        self.assertFalse(duplicate)
        self.assertEqual([alert.source for alert in created], ["source-a", "source-b"])

        repeated, duplicate = create_alert_batch(
            kind="notification",
            due_at=now,
            message="Door open.",
            sources=["source-a", "source-b"],
            session_id=None,
            idempotency_key="notification:door_open:event-1",
        )
        self.assertTrue(duplicate)
        self.assertEqual(repeated, [])

    def test_notification_delivery_can_defer_then_deliver(self) -> None:
        now = datetime.now().astimezone()
        created, _duplicate = create_alert_batch(
            kind="notification",
            due_at=now,
            message="Door open.",
            sources=["source-a"],
            session_id=None,
            expires_at=now + timedelta(seconds=90),
        )
        alert_id = created[0].alert_id

        self.assertEqual(consume_due_alerts("source-a"), [])
        delivered = consume_due_alerts(
            "source-a",
            notification_decisions={alert_id: "deliver"},
        )
        self.assertEqual([item["message"] for item in delivered], ["Door open."])
