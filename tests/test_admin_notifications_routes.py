from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Request

from oracle_app.admin_notifications_routes import (
    admin_notification_deliveries,
    admin_notifications_overview,
    admin_notifications_overview_http,
    register_admin_notifications_routes,
)
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.domain_models import NotificationType


SETTINGS = {
    "notifications": {
        "side_entry_open": {
            "id": "side_entry_open",
            "enabled": True,
            "message": "Do not expose rendered content here.",
            "targets": ["living_room_satellite"],
            "external_delivery": {
                "enabled": False,
                "recipient_groups": [],
                "delivery_ttl_seconds": 300,
                "max_attempts": 3,
                "retry_seconds": 30,
                "quiet_hours_policy": "bypass",
                "repeat_policy": "first_per_correlation",
                "failure_policy": "best_effort",
            },
        }
    },
    "recipient_groups": {
        "primary": {
            "id": "primary",
            "enabled": False,
            "provider": "apprise",
            "config_key": "oracle",
            "routing_tag": "private_tag",
        }
    },
}

DELIVERY = {
    "receipt_id": "delivery-1",
    "created_at": "2026-06-29T00:00:00+00:00",
    "updated_at": "2026-06-29T00:00:00+00:00",
    "notification_type": "side_entry_open",
    "occurrence_id": "run-1:1",
    "correlation_id": "run-1",
    "channel": "external",
    "destination_id": "primary",
    "provider": "apprise",
    "status": "pending",
    "attempt_count": 0,
    "max_attempts": 3,
    "next_attempt_at": None,
    "expires_at": "2026-06-29T00:05:00+00:00",
    "accepted_at": None,
    "completed_at": None,
    "failure_policy": "best_effort",
    "repeat_policy": "first_per_correlation",
    "last_error_class": "",
    "last_error_code": "",
    "provider_url": "https://forbidden.invalid",
}


class AdminNotificationsRouteTests(unittest.TestCase):
    @patch("oracle_app.admin_notifications_routes.summarize_notification_deliveries")
    @patch("oracle_app.admin_notifications_routes.list_notification_deliveries")
    @patch("oracle_app.admin_notifications_routes.AppriseBridge")
    @patch("oracle_app.admin_notifications_routes.get_apprise_settings")
    @patch("oracle_app.admin_notifications_routes.get_notification_settings")
    def test_overview_is_sanitized_and_read_only(
        self,
        mock_notifications,
        mock_apprise_settings,
        bridge_class,
        mock_list,
        mock_summary,
    ) -> None:
        mock_notifications.return_value = SETTINGS
        mock_apprise_settings.return_value = {
            "enabled": True,
            "base_url": "http://127.0.0.1:8020",
        }
        bridge_class.return_value.check_health.return_value = {
            "status": "ok",
            "provider": "apprise",
            "configured": True,
            "available": True,
        }
        mock_list.return_value = [DELIVERY]
        mock_summary.return_value = {"total": 1, "by_status": {"pending": 1}}

        payload = admin_notifications_overview()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["provider"]["enabled"])
        self.assertEqual(payload["summary"]["deliveries"]["total"], 1)
        self.assertNotIn("message", payload["definitions"][0])
        self.assertNotIn("targets", payload["definitions"][0])
        self.assertNotIn("config_key", payload["recipient_groups"][0])
        self.assertNotIn("routing_tag", payload["recipient_groups"][0])
        self.assertNotIn("provider_url", payload["recent_deliveries"][0])

    @patch("oracle_app.admin_notifications_routes.summarize_notification_deliveries")
    @patch("oracle_app.admin_notifications_routes.list_notification_deliveries")
    def test_delivery_history_applies_external_filters(self, mock_list, mock_summary) -> None:
        mock_list.return_value = [DELIVERY]
        mock_summary.return_value = {"total": 1, "by_status": {"pending": 1}}

        payload = admin_notification_deliveries(
            notification_type="SIDE_ENTRY_OPEN",
            status="PENDING",
            limit=900,
            offset=-4,
        )

        query = mock_list.call_args.args[0]
        self.assertEqual(query.notification_type, "side_entry_open")
        self.assertEqual(query.channel, "external")
        self.assertEqual(query.status, "pending")
        self.assertEqual(query.limit, 500)
        self.assertEqual(query.offset, 0)
        self.assertEqual(payload["deliveries"][0]["receipt_id"], "delivery-1")

    def test_delivery_history_rejects_unknown_status(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            admin_notification_deliveries(status="unknown")
        self.assertEqual(raised.exception.status_code, 422)

    def test_routes_register_expected_paths(self) -> None:
        app = FastAPI()
        register_admin_notifications_routes(app)
        paths = {route.path for route in app.routes}

        self.assertIn("/api/admin/notifications", paths)
        self.assertIn("/api/admin/notifications/deliveries", paths)

    @patch("oracle_app.admin_notifications_routes.summarize_notification_deliveries")
    @patch("oracle_app.admin_notifications_routes.list_notification_deliveries")
    @patch("oracle_app.admin_notifications_routes.get_apprise_settings")
    @patch("oracle_app.admin_notifications_routes.get_notification_settings")
    def test_overview_http_selects_immutable_canonical_view(
        self,
        legacy_notifications,
        legacy_apprise,
        mock_list,
        mock_summary,
    ) -> None:
        definition = NotificationType.model_validate(
            {
                "id": "door_open",
                "enabled": True,
                "message": "Do not expose this message.",
                "audience": [],
                "suppressed_by": [],
                "delivery_ttl_seconds": 90,
                "audio_policy": "pause_resume",
            }
        )
        settings = SimpleNamespace(
            config_revision="oracle-config-v1:sha256:canonical",
            types={"door_open": SimpleNamespace(definition=definition)},
            recipient_groups={},
            providers={},
        )
        application = FastAPI()
        application.state.brain_application_composition = CanonicalBrainApplicationComposition(
            runtime=SimpleNamespace(notifications=settings),  # type: ignore[arg-type]
            core_consumers=Mock(),
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
        )
        mock_list.return_value = []
        mock_summary.return_value = {"total": 0, "by_status": {}}

        payload = admin_notifications_overview_http(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/admin/notifications",
                    "query_string": b"",
                    "headers": [],
                    "app": application,
                }
            )
        )

        self.assertEqual(payload["configuration_revision"], settings.config_revision)
        self.assertEqual(payload["definitions"][0]["id"], "door_open")
        self.assertNotIn("message", payload["definitions"][0])
        legacy_notifications.assert_not_called()
        legacy_apprise.assert_not_called()


if __name__ == "__main__":
    unittest.main()
