from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.wake_arbitration import WakeArbitrationService, WakeClaim


class WakeArbitrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)

    def test_one_wake_event_wins_after_window(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        receipt = service.submit_claim(
            WakeClaim(satellite_id="bedroom-satellite", wake_confidence=0.72, audio_level=0.2),
            received_at=self.now,
        )

        self.assertIsNone(service.decision_for_claim(receipt.claim_id, now=self.now + timedelta(milliseconds=499)))

        decision = service.decision_for_claim(receipt.claim_id, now=self.now + timedelta(milliseconds=500))

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.decision, "proceed")
        self.assertEqual(decision.satellite_id, "bedroom-satellite")
        self.assertEqual(decision.winner_satellite_id, "bedroom-satellite")
        self.assertEqual(decision.participants, ("bedroom-satellite",))

    def test_two_wake_events_within_window_choose_louder_satellite(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        quieter = service.submit_claim(
            WakeClaim(satellite_id="bedroom-satellite", wake_confidence=0.95, audio_level=0.2),
            received_at=self.now,
        )
        louder = service.submit_claim(
            WakeClaim(satellite_id="hallway-satellite", wake_confidence=0.70, audio_level=0.6),
            received_at=self.now + timedelta(milliseconds=300),
        )

        decision_for_quieter = service.decision_for_claim(quieter.claim_id, now=self.now + timedelta(milliseconds=500))
        decision_for_louder = service.decision_for_claim(louder.claim_id, now=self.now + timedelta(milliseconds=500))

        self.assertEqual(decision_for_quieter.decision, "stand_down")
        self.assertEqual(decision_for_louder.decision, "proceed")
        self.assertEqual(decision_for_louder.winner_satellite_id, "hallway-satellite")
        self.assertEqual(decision_for_louder.reason, "highest_audio_level")
        self.assertEqual(decision_for_louder.participants, ("bedroom-satellite", "hallway-satellite"))

    def test_missing_audio_level_falls_back_to_confidence(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        low_confidence = service.submit_claim(
            WakeClaim(satellite_id="bedroom-satellite", wake_confidence=0.65),
            received_at=self.now,
        )
        high_confidence = service.submit_claim(
            WakeClaim(satellite_id="hallway-satellite", wake_confidence=0.91),
            received_at=self.now + timedelta(milliseconds=100),
        )

        decision_for_low = service.decision_for_claim(low_confidence.claim_id, now=self.now + timedelta(milliseconds=500))
        decision_for_high = service.decision_for_claim(high_confidence.claim_id, now=self.now + timedelta(milliseconds=500))

        self.assertEqual(decision_for_low.decision, "stand_down")
        self.assertEqual(decision_for_high.decision, "proceed")
        self.assertEqual(decision_for_high.winner_satellite_id, "hallway-satellite")
        self.assertEqual(decision_for_high.reason, "highest_wake_confidence")

    def test_missing_audio_and_confidence_falls_back_to_most_recent(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        older = service.submit_claim(WakeClaim(satellite_id="bedroom-satellite"), received_at=self.now)
        newer = service.submit_claim(
            WakeClaim(satellite_id="hallway-satellite"),
            received_at=self.now + timedelta(milliseconds=250),
        )

        decision_for_older = service.decision_for_claim(older.claim_id, now=self.now + timedelta(milliseconds=500))
        decision_for_newer = service.decision_for_claim(newer.claim_id, now=self.now + timedelta(milliseconds=500))

        self.assertEqual(decision_for_older.decision, "stand_down")
        self.assertEqual(decision_for_newer.decision, "proceed")
        self.assertEqual(decision_for_newer.winner_satellite_id, "hallway-satellite")
        self.assertEqual(decision_for_newer.reason, "most_recent")

    def test_events_outside_window_are_separate_interactions(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        first = service.submit_claim(
            WakeClaim(satellite_id="bedroom-satellite", audio_level=0.2),
            received_at=self.now,
        )
        second = service.submit_claim(
            WakeClaim(satellite_id="hallway-satellite", audio_level=0.9),
            received_at=self.now + timedelta(milliseconds=501),
        )

        first_decision = service.decision_for_claim(first.claim_id, now=self.now + timedelta(milliseconds=501))
        second_decision = service.decision_for_claim(second.claim_id, now=self.now + timedelta(milliseconds=1001))

        self.assertNotEqual(first.interaction_id, second.interaction_id)
        self.assertEqual(first_decision.decision, "proceed")
        self.assertEqual(first_decision.winner_satellite_id, "bedroom-satellite")
        self.assertEqual(first_decision.participants, ("bedroom-satellite",))
        self.assertEqual(second_decision.decision, "proceed")
        self.assertEqual(second_decision.winner_satellite_id, "hallway-satellite")
        self.assertEqual(second_decision.participants, ("hallway-satellite",))

    def test_finalize_due_returns_all_decisions_for_due_windows(self) -> None:
        service = WakeArbitrationService(window_ms=500)
        service.submit_claim(WakeClaim(satellite_id="bedroom-satellite", audio_level=0.2), received_at=self.now)
        service.submit_claim(
            WakeClaim(satellite_id="hallway-satellite", audio_level=0.9),
            received_at=self.now + timedelta(milliseconds=100),
        )

        decisions = service.finalize_due(now=self.now + timedelta(milliseconds=500))

        self.assertEqual(len(decisions), 2)
        self.assertEqual(
            {decision.satellite_id: decision.decision for decision in decisions},
            {
                "bedroom-satellite": "stand_down",
                "hallway-satellite": "proceed",
            },
        )


if __name__ == "__main__":
    unittest.main()
