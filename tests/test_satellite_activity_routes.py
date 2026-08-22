from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.schemas import SatelliteActivityRequest
from oracle_app.satellite_activity_routes import satellite_activity


class SatelliteActivityRoutesTests(unittest.TestCase):
    @patch("oracle_app.satellite_activity_routes.get_correlation_id", return_value="generated-correlation")
    @patch("oracle_app.satellite_activity_routes.observe_satellite_activity")
    def test_satellite_activity_accepts_and_writes_payload(self, mock_observe, _mock_correlation) -> None:
        payload = SatelliteActivityRequest(
            source_id="test_satellite_bravo",
            event_type="wake_detected",
            status="available",
            observed_at="2026-05-12T12:00:00Z",
            payload={"wake_score": 0.8},
            snapshot={"last_seen_at": "2026-05-12T12:00:00Z"},
        )

        response = satellite_activity(payload, SimpleNamespace(headers={}))

        self.assertEqual(response.model_dump(), {"accepted": True})
        mock_observe.assert_called_once_with(
            source_id="test_satellite_bravo",
            event_type="wake_detected",
            status="available",
            correlation_id="generated-correlation",
            observed_at="2026-05-12T12:00:00Z",
            payload={"wake_score": 0.8},
            snapshot={"last_seen_at": "2026-05-12T12:00:00Z"},
        )

    @patch("oracle_app.satellite_activity_routes.observe_satellite_activity")
    def test_satellite_activity_uses_header_correlation_id(self, mock_observe) -> None:
        payload = SatelliteActivityRequest(source_id="test_satellite_bravo", event_type="wake_detected")
        request = SimpleNamespace(headers={"X-Oracle-Correlation-Id": "header-correlation"})

        response = satellite_activity(payload, request)

        self.assertEqual(response.model_dump(), {"accepted": True})
        self.assertEqual(mock_observe.call_args.kwargs["correlation_id"], "header-correlation")

    @patch("oracle_app.satellite_activity_routes.observe_satellite_activity")
    def test_satellite_activity_rejects_correlation_mismatch(self, mock_observe) -> None:
        payload = SatelliteActivityRequest(
            source_id="test_satellite_bravo",
            event_type="wake_detected",
            correlation_id="body-correlation",
        )
        request = SimpleNamespace(headers={"X-Oracle-Correlation-Id": "header-correlation"})

        with self.assertRaises(HTTPException) as context:
            satellite_activity(payload, request)

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "Correlation ID header/body mismatch")
        mock_observe.assert_not_called()

    @patch("oracle_app.satellite_activity_routes.observe_satellite_activity", side_effect=ValueError("bad event"))
    def test_satellite_activity_maps_validation_error_to_422(self, _mock_observe) -> None:
        payload = SatelliteActivityRequest(source_id="test_satellite_bravo", event_type="bad_event")

        with self.assertRaises(HTTPException) as context:
            satellite_activity(payload, SimpleNamespace(headers={}))

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "bad event")

    @patch("oracle_app.satellite_activity_routes.observe_satellite_activity", side_effect=RuntimeError("db unavailable"))
    def test_satellite_activity_fails_open_on_unexpected_write_error(self, _mock_observe) -> None:
        payload = SatelliteActivityRequest(source_id="test_satellite_bravo", event_type="wake_detected")

        response = satellite_activity(payload, SimpleNamespace(headers={}))

        self.assertEqual(response.model_dump(), {"accepted": True})


if __name__ == "__main__":
    unittest.main()
