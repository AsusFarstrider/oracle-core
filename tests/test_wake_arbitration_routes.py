from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.schemas import WakeClaimRequest
from oracle_app.wake_arbitration import WakeArbitrationService
from oracle_app.wake_arbitration_routes import wake_claim


class WakeArbitrationRoutesTests(unittest.TestCase):
    def test_wake_claim_returns_proceed_for_single_claim(self) -> None:
        service = WakeArbitrationService(window_ms=0)
        payload = WakeClaimRequest(
            satellite_id="bedroom-satellite",
            wake_confidence=0.85,
            audio_level=0.4,
            correlation_id="corr-test-1",
        )

        with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_settings", return_value={"window_ms": 0}):
            with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_service", return_value=service):
                with patch("oracle_app.wake_arbitration_routes.get_source_registry", return_value={}):
                    response = wake_claim(payload, SimpleNamespace(headers={}))

        self.assertEqual(response.decision, "proceed")
        self.assertEqual(response.satellite_id, "bedroom-satellite")
        self.assertEqual(response.winner_satellite_id, "bedroom-satellite")
        self.assertEqual(response.participants, ["bedroom-satellite"])

    def test_wake_claim_losing_satellite_receives_stand_down(self) -> None:
        service = WakeArbitrationService(window_ms=50)
        results: dict[str, object] = {}
        original_submit_claim = service.submit_claim
        first_claim_submitted = threading.Event()

        def submit(name: str, audio_level: float) -> None:
            payload = WakeClaimRequest(satellite_id=name, audio_level=audio_level, correlation_id=f"corr-{name}")
            results[name] = wake_claim(payload, SimpleNamespace(headers={}))

        def submit_claim_and_signal(*args, **kwargs):
            receipt = original_submit_claim(*args, **kwargs)
            claim = args[0]
            if claim.satellite_id == "bedroom-satellite":
                first_claim_submitted.set()
            return receipt

        with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_settings", return_value={"window_ms": 50}):
            with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_service", return_value=service):
                with patch("oracle_app.wake_arbitration_routes.get_source_registry", return_value={}):
                    with patch.object(service, "submit_claim", side_effect=submit_claim_and_signal):
                        first = threading.Thread(target=submit, args=("bedroom-satellite", 0.2))
                        first.start()
                        self.assertTrue(first_claim_submitted.wait(timeout=1.0))
                        submit("hallway-satellite", 0.9)
                        first.join(timeout=1.0)

        bedroom = results["bedroom-satellite"]
        hallway = results["hallway-satellite"]
        self.assertEqual(bedroom.decision, "stand_down")
        self.assertEqual(bedroom.winner_satellite_id, "hallway-satellite")
        self.assertEqual(hallway.decision, "proceed")
        self.assertEqual(hallway.winner_satellite_id, "hallway-satellite")
        self.assertEqual(hallway.participants, ["bedroom-satellite", "hallway-satellite"])

    def test_wake_claim_enriches_missing_room_and_profile_from_source_registry(self) -> None:
        service = WakeArbitrationService(window_ms=0)
        payload = WakeClaimRequest(satellite_id="bedroom-satellite", wake_confidence=0.8)

        with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_settings", return_value={"window_ms": 0}):
            with patch("oracle_app.wake_arbitration_routes.get_wake_arbitration_service", return_value=service):
                with patch(
                    "oracle_app.wake_arbitration_routes.get_source_registry",
                    return_value={
                        "bedroom-satellite": {
                            "default_room": "bedroom",
                            "ui": {"profile": "bedroom_touch_v1"},
                        }
                    },
                ):
                    response = wake_claim(payload, SimpleNamespace(headers={}))

        self.assertEqual(response.room_id, "bedroom")
        self.assertEqual(response.profile, "bedroom_touch_v1")

    def test_wake_claim_rejects_correlation_mismatch(self) -> None:
        payload = WakeClaimRequest(satellite_id="bedroom-satellite", correlation_id="body-correlation")
        request = SimpleNamespace(headers={"X-Oracle-Correlation-Id": "header-correlation"})

        with self.assertRaises(HTTPException) as context:
            wake_claim(payload, request)

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "Correlation ID header/body mismatch")


if __name__ == "__main__":
    unittest.main()
