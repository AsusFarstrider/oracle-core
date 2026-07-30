from __future__ import annotations

import sys
import unittest
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
sys.modules.setdefault("python_multipart", python_multipart_stub)

from oracle_app.api import health_config


class HealthConfigTests(unittest.TestCase):
    def _build_request(
        self,
        *,
        query_string: bytes = b"",
        accept: str | None = None,
    ) -> Request:
        headers: list[tuple[bytes, bytes]] = []
        if accept is not None:
            headers.append((b"accept", accept.encode("utf-8")))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health/config",
            "query_string": query_string,
            "headers": headers,
        }
        return Request(scope)

    def test_health_config_returns_json_by_default(self) -> None:
        findings = [
            {
                "subsystem": "brain",
                "setting": "server_config_local_json",
                "severity": "warning",
                "status": "deprecated_local_truth",
                "effective_source": "local_config",
                "message": "server/config.local.json still contains deploy-specific values.",
            }
        ]

        with patch("oracle_app.health_routes.build_brain_config_report", return_value=findings):
            response = health_config(self._build_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["has_warnings"])
        self.assertEqual(payload["service"], "oracle-brain")
        self.assertEqual(payload["sections"][0]["heading"], "Brain config check:")
        self.assertEqual(payload["sections"][0]["findings"][0]["status"], "deprecated_local_truth")

    def test_health_config_returns_text_when_requested(self) -> None:
        findings = [
            {
                "subsystem": "brain",
                "setting": "ORACLE_UNUSED",
                "severity": "warning",
                "status": "unknown_env",
                "effective_source": "env",
                "message": "Unknown Oracle environment variable: ORACLE_UNUSED",
            }
        ]

        with patch("oracle_app.health_routes.build_brain_config_report", return_value=findings):
            response = health_config(self._build_request(query_string=b"format=text"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/plain")
        self.assertIn("Brain config check:", response.body.decode("utf-8"))
        self.assertIn("ORACLE_UNUSED", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
