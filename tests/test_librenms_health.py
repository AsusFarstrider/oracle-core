from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib import error
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.provider_bridges.librenms import LibreNmsBridge


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class LibreNmsHealthTests(unittest.TestCase):
    def test_unconfigured_reports_missing_keys(self) -> None:
        result = LibreNmsBridge().check_health(settings={"enabled": False, "missing_config_keys": []})

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["configured"])
        self.assertFalse(result["available"])
        self.assertEqual(
            result["missing_config_keys"],
            [
                "ORACLE_LIBRENMS_TOKEN/librenms_token",
                "ORACLE_LIBRENMS_URL/librenms_url",
            ],
        )

    @patch("oracle_app.provider_bridges.librenms.request.urlopen")
    def test_configured_success_is_read_only_and_available(self, mock_urlopen) -> None:
        seen = {}

        def _urlopen(req, timeout):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["token_header_present"] = bool(req.get_header("X-auth-token"))
            seen["timeout"] = timeout
            return _FakeResponse({"alerts": []})

        mock_urlopen.side_effect = _urlopen

        result = LibreNmsBridge().check_health(
            settings={
                "enabled": True,
                "base_url": "http://librenms.local/",
                "api_token": "secret-token",
                "timeout_seconds": 9,
                "missing_config_keys": [],
            }
        )

        self.assertEqual(seen["url"], "http://librenms.local/api/v0/alerts")
        self.assertEqual(seen["method"], "GET")
        self.assertTrue(seen["token_header_present"])
        self.assertEqual(seen["timeout"], 9)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["configured"])
        self.assertTrue(result["available"])
        self.assertFalse(result["degraded"])
        self.assertNotIn("secret-token", str(result))

    @patch("oracle_app.provider_bridges.librenms.request.urlopen")
    def test_auth_failure_is_unavailable_without_token_leakage(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = error.HTTPError(
            "http://librenms.local/api/v0/alerts",
            401,
            "Unauthorized",
            hdrs=None,
            fp=None,
        )

        result = LibreNmsBridge().check_health(
            settings={
                "enabled": True,
                "base_url": "http://librenms.local",
                "api_token": "secret-token",
                "timeout_seconds": 5,
                "missing_config_keys": [],
            }
        )

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["configured"])
        self.assertFalse(result["available"])
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("secret-token", str(result))

    @patch("oracle_app.provider_bridges.librenms.request.urlopen")
    def test_monitoring_status_degrades_when_alerts_have_only_name_fields(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse(
            {
                "alerts": [
                    {
                        "hostname": "edge-router.example",
                        "severity": "critical",
                        "name": "Service up/down",
                    }
                ]
            }
        )

        result = LibreNmsBridge().get_monitoring_status(
            settings={
                "enabled": True,
                "base_url": "http://librenms.local",
                "api_token": "secret-token",
                "timeout_seconds": 5,
            }
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["alert_count"], 1)
        self.assertEqual(result["problems"], ["Service up/down on edge-router.example is critical."])


if __name__ == "__main__":
    unittest.main()
