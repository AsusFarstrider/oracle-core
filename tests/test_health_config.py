from __future__ import annotations

import sys
import unittest
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
sys.modules.setdefault("python_multipart", python_multipart_stub)

from oracle_app.brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from oracle_app.api import health_config


class HealthConfigTests(unittest.TestCase):
    def _build_request(
        self,
        *,
        query_string: bytes = b"",
        accept: str | None = None,
    ) -> Request:
        composition = MagicMock(spec=CanonicalBrainApplicationComposition)
        composition.applied_configuration_payload.return_value = {
            "mode": "canonical",
            "applied_generation": {
                "activation_generation_id": "activation_test",
                "config_generation_id": "config_test",
                "secret_generation_id": "secret_test",
                "config_revision": "revision_test",
                "selection_operation_id": "selection_test",
                "selection_revision": 1,
                "satellite_projection_activation_ids": {},
            },
        }
        state = SimpleNamespace()
        setattr(state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, composition)
        headers: list[tuple[bytes, bytes]] = []
        if accept is not None:
            headers.append((b"accept", accept.encode("utf-8")))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health/config",
            "query_string": query_string,
            "headers": headers,
            "app": SimpleNamespace(state=state),
        }
        return Request(scope)

    def test_health_config_returns_json_by_default(self) -> None:
        response = health_config(self._build_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["has_warnings"])
        self.assertEqual(payload["service"], "oracle-brain")
        self.assertEqual(payload["sections"], [])
        self.assertEqual(payload["configuration"]["mode"], "canonical")

    def test_health_config_returns_text_when_requested(self) -> None:
        response = health_config(self._build_request(query_string=b"format=text"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/plain")
        self.assertIn("Applied configuration:", response.body.decode("utf-8"))
        self.assertIn("activation_generation_id: activation_test", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
