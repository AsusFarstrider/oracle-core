from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from alert_store_test_support import IsolatedAlertStoreTestCase
from oracle_app.alerts import (
    consume_due_alerts,
    create_alert_batch,
    list_due_alerts,
    record_alert_idempotency_key,
)
from oracle_app.notifications import (
    NotificationContextNotSupportedError,
    NotificationRequestError,
    build_notification_delivery_decisions,
    evaluate_notification_suppression,
    submit_notification,
)


SETTINGS = {
    "version": 1,
    "modes": {
        "quiet": {
            "id": "quiet",
            "entity_id": "input_boolean.quiet_mode",
            "active_state": "on",
        }
    },
    "notifications": {
        "side_entry_open": {
            "id": "side_entry_open",
            "enabled": True,
            "message": "The side entry is still open. Please close it.",
            "targets": ["living_room_satellite", "kitchen_display"],
            "suppressed_by": ["quiet"],
            "delivery_ttl_seconds": 90,
            "audio_policy": "pause_resume",
        }
    },
}


def _external_settings(*, failure_policy: str = "best_effort"):
    settings = deepcopy(SETTINGS)
    settings["recipient_groups"] = {
        "primary": {
            "id": "primary",
            "enabled": True,
            "provider": "apprise",
            "config_key": "oracle",
            "routing_tag": "primary",
        }
    }
    settings["notifications"]["side_entry_open"]["external_delivery"] = {
        "enabled": True,
        "recipient_groups": ["primary"],
        "delivery_ttl_seconds": 300,
        "max_attempts": 3,
        "retry_seconds": 30,
        "quiet_hours_policy": "bypass",
        "repeat_policy": "first_per_correlation",
        "failure_policy": failure_policy,
    }
    return settings


class NotificationAlertStoreTests(IsolatedAlertStoreTestCase):

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
        self.assertEqual(len(list_due_alerts("source-a", kind="notification")), 1)
        self.assertEqual(len(list_due_alerts("source-b", kind="notification")), 1)

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

    def test_expired_notification_is_not_delivered_and_remains_deduplicated(self) -> None:
        now = datetime.now().astimezone()
        create_alert_batch(
            kind="notification",
            due_at=now - timedelta(seconds=2),
            message="Stale door warning.",
            sources=["source-a"],
            session_id=None,
            expires_at=now - timedelta(seconds=1),
            idempotency_key="notification:door_open:event-expired",
        )

        self.assertEqual(
            consume_due_alerts("source-a", notification_decisions={}),
            [],
        )
        _created, duplicate = create_alert_batch(
            kind="notification",
            due_at=now,
            message="Stale door warning.",
            sources=["source-a"],
            session_id=None,
            idempotency_key="notification:door_open:event-expired",
        )
        self.assertTrue(duplicate)

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
        self.assertEqual(len(list_due_alerts("source-a", kind="notification")), 1)
        delivered = consume_due_alerts(
            "source-a",
            notification_decisions={alert_id: "deliver"},
        )
        self.assertEqual([item["message"] for item in delivered], ["Door open."])

    def test_suppressed_receipt_deduplicates_later_fanout(self) -> None:
        key = "notification:door_open:suppressed-event"

        self.assertFalse(record_alert_idempotency_key(key))
        _created, duplicate = create_alert_batch(
            kind="notification",
            due_at=datetime.now().astimezone(),
            message="Door open.",
            sources=["source-a"],
            session_id=None,
            idempotency_key=key,
        )

        self.assertTrue(duplicate)


class NotificationServiceTests(unittest.TestCase):
    def test_suppression_reads_home_assistant_helper(self) -> None:
        bridge = SimpleNamespace(
            fetch_entity_state=lambda entity_id: {
                "entity_id": entity_id,
                "state": "on",
            }
        )
        with patch("oracle_app.notifications.policy.get_home_assistant_settings", return_value=("http://ha", "token")), patch(
            "oracle_app.notifications.policy.HomeAssistantBridge",
            return_value=bridge,
        ):
            status = evaluate_notification_suppression(
                SETTINGS["notifications"]["side_entry_open"],
                settings=SETTINGS,
            )

        self.assertEqual(status, "active")

    def test_unknown_helper_state_fails_silent(self) -> None:
        bridge = SimpleNamespace(fetch_entity_state=lambda _entity_id: None)
        with patch("oracle_app.notifications.policy.get_home_assistant_settings", return_value=("http://ha", "token")), patch(
            "oracle_app.notifications.policy.HomeAssistantBridge",
            return_value=bridge,
        ):
            status = evaluate_notification_suppression(
                SETTINGS["notifications"]["side_entry_open"],
                settings=SETTINGS,
            )

        self.assertEqual(status, "unavailable")

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch")
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.catalog.get_notification_settings", return_value=SETTINGS)
    def test_submit_resolves_text_and_targets_from_config(
        self,
        _mock_settings,
        _mock_suppression,
        mock_create,
        _mock_record,
    ) -> None:
        now = datetime.now().astimezone()
        mock_create.return_value = (
            [SimpleNamespace(source="living_room_satellite"), SimpleNamespace(source="kitchen_display")],
            False,
        )
        with patch("oracle_app.notifications.service._now_local", return_value=now):
            result = submit_notification(
                "side_entry_open",
                "event-1",
                caller="home_assistant",
            )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["queued_targets"], ["living_room_satellite", "kitchen_display"])
        self.assertEqual(mock_create.call_args.kwargs["message"], SETTINGS["notifications"]["side_entry_open"]["message"])
        self.assertEqual(mock_create.call_args.kwargs["idempotency_key"], "notification:side_entry_open:event-1")
        self.assertNotIn("caller", mock_create.call_args.kwargs["metadata"])

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.service.record_alert_idempotency_key", return_value=False)
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="active")
    @patch("oracle_app.notifications.catalog.get_notification_settings", return_value=SETTINGS)
    def test_submit_returns_suppressed_without_creating_alerts(
        self,
        _mock_settings,
        _mock_suppression,
        mock_receipt,
        _mock_record,
    ) -> None:
        with patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch") as mock_create:
            result = submit_notification(
                "side_entry_open",
                "event-2",
                caller="home_assistant",
            )

        self.assertEqual(result["status"], "suppressed")
        mock_create.assert_not_called()
        mock_receipt.assert_called_once()

    @patch("oracle_app.notifications.channels.satellite_announcement.record_notification_event")
    @patch("oracle_app.notifications.channels.satellite_announcement.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.channels.satellite_announcement.get_notification_settings", return_value=SETTINGS)
    @patch("oracle_app.notifications.channels.satellite_announcement.list_due_alerts")
    def test_delivery_decision_uses_current_suppression_state(
        self,
        mock_due,
        _mock_settings,
        _mock_suppression,
        _mock_record,
    ) -> None:
        mock_due.return_value = [
            SimpleNamespace(
                alert_id="alert-1",
                source="living_room_satellite",
                expires_at=datetime.now().astimezone() + timedelta(seconds=60),
                metadata={"notification_id": "side_entry_open", "event_id": "event-1"},
            )
        ]

        decisions = build_notification_delivery_decisions("living_room_satellite")

        self.assertEqual(decisions, {"alert-1": "deliver"})

    def test_provider_neutral_emit_rejects_undeclared_context(self) -> None:
        with self.assertRaises(NotificationContextNotSupportedError):
            submit_notification(
                "side_entry_open",
                "run-1:step-1",
                caller="composite_runbook",
                context={"door": "back"},
            )

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch")
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.catalog.get_notification_settings", return_value=SETTINGS)
    def test_provider_neutral_emit_records_caller_and_correlation(
        self,
        _mock_settings,
        _mock_suppression,
        mock_create,
        mock_record,
    ) -> None:
        mock_create.return_value = ([SimpleNamespace(source="living_room_satellite")], False)

        result = submit_notification(
            "side_entry_open",
            "run-1:step-1",
            caller="composite_runbook",
            correlation_id="run-1",
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(
            mock_create.call_args.kwargs["metadata"]["caller"],
            "composite_runbook",
        )
        mock_record.assert_called_once_with(
            notification_type="side_entry_open",
            occurrence_id="run-1:step-1",
            status="queued",
            caller="composite_runbook",
            target_count=1,
            correlation_id="run-1",
        )

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.service.reserve_external_deliveries")
    @patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch")
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.catalog.get_notification_settings")
    def test_submit_reports_durable_external_channel_work(
        self,
        mock_settings,
        _mock_suppression,
        mock_create,
        mock_external,
        _mock_record,
    ) -> None:
        mock_settings.return_value = _external_settings()
        mock_create.return_value = ([SimpleNamespace(source="living_room_satellite")], False)
        mock_external.return_value = {
            "channel": "external",
            "status": "queued",
            "receipt_ids": ["delivery-1"],
            "queued_count": 1,
            "duplicate_count": 0,
        }

        result = submit_notification(
            "side_entry_open",
            "run-1:1",
            caller="home_automation_runbook",
            correlation_id="run-1",
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["channel_results"]["external"]["receipt_ids"], ["delivery-1"])
        self.assertEqual(
            result["channel_results"]["satellite_announcement"]["status"],
            "queued",
        )

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.service.reserve_external_deliveries", side_effect=OSError("disk"))
    @patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch")
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.catalog.get_notification_settings")
    def test_best_effort_external_reservation_failure_is_partial(
        self,
        mock_settings,
        _mock_suppression,
        mock_create,
        _mock_external,
        _mock_record,
    ) -> None:
        mock_settings.return_value = _external_settings()
        mock_create.return_value = ([SimpleNamespace(source="living_room_satellite")], False)

        result = submit_notification(
            "side_entry_open",
            "run-1:1",
            correlation_id="run-1",
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["channel_results"]["external"]["status"], "failed")
        self.assertEqual(result["queued_targets"], ["living_room_satellite"])

    @patch("oracle_app.notifications.service.record_notification_event")
    @patch("oracle_app.notifications.service.reserve_external_deliveries", side_effect=OSError("disk"))
    @patch("oracle_app.notifications.channels.satellite_announcement.create_alert_batch")
    @patch("oracle_app.notifications.service.evaluate_notification_suppression", return_value="inactive")
    @patch("oracle_app.notifications.catalog.get_notification_settings")
    def test_required_external_reservation_failure_rejects_submission(
        self,
        mock_settings,
        _mock_suppression,
        mock_create,
        _mock_external,
        _mock_record,
    ) -> None:
        mock_settings.return_value = _external_settings(failure_policy="required")
        mock_create.return_value = ([SimpleNamespace(source="living_room_satellite")], False)

        with self.assertRaisesRegex(
            NotificationRequestError,
            "Required external notification work could not be accepted",
        ):
            submit_notification(
                "side_entry_open",
                "run-1:1",
                correlation_id="run-1",
            )


if __name__ == "__main__":
    unittest.main()
