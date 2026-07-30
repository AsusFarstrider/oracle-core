from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib import error

from oracle_app.provider_bridges.apprise import (
    AppriseBridge,
    AppriseBridgeConfigurationError,
    AppriseBridgeHttpError,
    AppriseBridgeResponseError,
    AppriseBridgeUnreachableError,
)


class _Response:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return self.raw


SETTINGS = {
    "enabled": True,
    "configured": True,
    "base_url": "http://127.0.0.1:8020",
    "timeout_seconds": 8,
    "missing_config_keys": [],
}


class AppriseBridgeTests(unittest.TestCase):
    def test_health_fails_closed_without_configuration(self) -> None:
        result = AppriseBridge().check_health(settings={})

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["configured"])
        self.assertFalse(result["available"])

    @patch("oracle_app.provider_bridges.apprise.request.urlopen")
    def test_health_returns_only_sanitized_status(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "config_lock": True,
                "attach_lock": True,
                "status": {"details": ["OK"]},
                "provider_urls": ["ntfy://must-not-escape"],
            }
        )

        result = AppriseBridge().check_health(settings=SETTINGS)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["configuration_locked"])
        self.assertTrue(result["attachments_locked"])
        self.assertEqual(result["provider_details"], ["OK"])
        self.assertNotIn("provider_urls", result)

    @patch("oracle_app.provider_bridges.apprise.request.urlopen")
    def test_send_uses_stateful_key_and_tag(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response({}, status_code=200)

        result = AppriseBridge().send(
            settings=SETTINGS,
            config_key="oracle",
            routing_tag="primary",
            title="Oracle",
            body="The side entry is open.",
            notification_type="warning",
            body_format="text",
        )

        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8020/notify/oracle")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(
            json.loads(req.data),
            {
                "title": "Oracle",
                "body": "The side entry is open.",
                "type": "warning",
                "format": "text",
                "tag": "primary",
            },
        )
        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("routing_tag", result)

    def test_send_rejects_disabled_bridge(self) -> None:
        with self.assertRaises(AppriseBridgeConfigurationError):
            AppriseBridge().send(
                settings={**SETTINGS, "enabled": False},
                config_key="oracle",
                routing_tag="primary",
                title="Oracle",
                body="Test",
            )

    def test_send_rejects_unvalidated_key_or_tag(self) -> None:
        for config_key, routing_tag in (("../oracle", "primary"), ("oracle", "primary phone")):
            with self.subTest(config_key=config_key, routing_tag=routing_tag):
                with self.assertRaises(AppriseBridgeConfigurationError):
                    AppriseBridge().send(
                        settings=SETTINGS,
                        config_key=config_key,
                        routing_tag=routing_tag,
                        title="Oracle",
                        body="Test",
                    )

    @patch("oracle_app.provider_bridges.apprise.request.urlopen")
    def test_http_error_classifies_retryability_without_body(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = error.HTTPError(
            "http://127.0.0.1:8020/notify/oracle",
            503,
            "unavailable",
            {},
            None,
        )

        with self.assertRaises(AppriseBridgeHttpError) as raised:
            AppriseBridge().send(
                settings=SETTINGS,
                config_key="oracle",
                routing_tag="primary",
                title="Oracle",
                body="Test",
            )

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 503)

    @patch("oracle_app.provider_bridges.apprise.request.urlopen")
    def test_unreachable_and_invalid_responses_are_retryable(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = error.URLError("offline")
        with self.assertRaises(AppriseBridgeUnreachableError) as unreachable:
            AppriseBridge().send(
                settings=SETTINGS,
                config_key="oracle",
                routing_tag="primary",
                title="Oracle",
                body="Test",
            )
        self.assertTrue(unreachable.exception.retryable)

        mock_urlopen.side_effect = None
        mock_urlopen.return_value = _Response(b"not-json")
        with self.assertRaises(AppriseBridgeResponseError) as invalid:
            AppriseBridge().send(
                settings=SETTINGS,
                config_key="oracle",
                routing_tag="primary",
                title="Oracle",
                body="Test",
            )
        self.assertTrue(invalid.exception.retryable)


if __name__ == "__main__":
    unittest.main()
