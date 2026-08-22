from __future__ import annotations

import logging
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
requests_module = types.ModuleType("requests")
requests_module.post = lambda *args, **kwargs: None
requests_module.get = lambda *args, **kwargs: None
requests_module.RequestException = RuntimeError
sys.modules.setdefault("requests", requests_module)

from pi_runtime.models import CaptureOutcome, WakeArbitrationDecision
from pi_runtime.wake_arbitration import (
    ProvisionalCapture,
    WAKE_STATE_ACTIVE_LISTENING,
    WAKE_STATE_SUPPRESSED,
    arbitrate_provisional_capture,
    handle_wake_arbitration_loss,
    submit_wake_claim_for_decision,
)


def _capture_outcome(payload: bytes = b"pcm") -> CaptureOutcome:
    return CaptureOutcome(
        pcm_bytes=payload,
        stop_reason="silence",
        total_frames=3,
        voiced_frames=2,
        silence_frames=1,
        max_energy=0.4,
        noise_floor=0.01,
        speech_threshold=0.02,
        silence_threshold=0.01,
    )


class SatelliteWakeArbitrationTests(unittest.TestCase):
    def test_provisional_capture_starts_immediately_and_returns_result(self) -> None:
        started = []

        def capture_func() -> CaptureOutcome:
            started.append(time.time())
            return _capture_outcome(b"winner-pcm")

        provisional = ProvisionalCapture.start(capture_func)

        result = provisional.wait(timeout=1.0)

        self.assertTrue(started)
        self.assertEqual(result.pcm_bytes, b"winner-pcm")

    def test_provisional_capture_raises_capture_error(self) -> None:
        def capture_func() -> CaptureOutcome:
            raise RuntimeError("capture failed")

        provisional = ProvisionalCapture.start(capture_func)

        with self.assertRaises(RuntimeError) as context:
            provisional.wait(timeout=1.0)

        self.assertEqual(str(context.exception), "capture failed")

    def test_submit_wake_claim_for_decision_passes_satellite_metadata(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            wake_arbitration_timeout_seconds=0.75,
        )
        decision = WakeArbitrationDecision(
            interaction_id="wake-1",
            satellite_id="test_satellite_alpha",
            winner_satellite_id="test_satellite_alpha",
            decision="proceed",
            reason="highest_audio_level",
            participants=["test_satellite_alpha"],
            window_ms=500,
        )

        with patch("pi_runtime.wake_arbitration.submit_wake_claim", return_value=decision) as mock_submit:
            result = submit_wake_claim_for_decision(
                args=args,
                satellite_id="test_satellite_alpha",
                wake_confidence=0.82,
                audio_level=0.41,
                correlation_id="corr-test-1",
                timestamp="2026-05-23T12:00:00+00:00",
            )

        self.assertIs(result, decision)
        mock_submit.assert_called_once_with(
            "http://oracle",
            satellite_id="test_satellite_alpha",
            timestamp="2026-05-23T12:00:00+00:00",
            wake_confidence=0.82,
            audio_level=0.41,
            correlation_id="corr-test-1",
            credential="brain-token",
            timeout=0.75,
        )

    def test_arbitration_loss_reports_activity_and_enters_suppression(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_bravo",
            wake_arbitration_loser_suppression_ms=1500,
        )
        runtime_state = types.SimpleNamespace(next_wake_time=10.0)
        decision = WakeArbitrationDecision(
            interaction_id="wake-1",
            satellite_id="test_satellite_bravo",
            winner_satellite_id="test_satellite_alpha",
            decision="stand_down",
            reason="highest_audio_level",
            participants=["test_satellite_alpha", "test_satellite_bravo"],
            window_ms=500,
        )

        with patch("pi_runtime.wake_arbitration.report_satellite_activity") as mock_report:
            handle_wake_arbitration_loss(
                args=args,
                logger=logging.getLogger("satellite-wake-arbitration-test"),
                runtime_state=runtime_state,
                decision=decision,
                correlation_id="corr-test-1",
                wake_score=0.71,
                audio_level=0.2,
                now=100.0,
            )

        self.assertEqual(runtime_state.wake_state, WAKE_STATE_SUPPRESSED)
        self.assertEqual(runtime_state.wake_arbitration_suppressed_until, 101.5)
        self.assertEqual(runtime_state.next_wake_time, 101.5)
        mock_report.assert_called_once()
        self.assertEqual(mock_report.call_args.kwargs["event_type"], "wake_arbitration_lost")
        self.assertEqual(mock_report.call_args.kwargs["correlation_id"], "corr-test-1")
        self.assertEqual(mock_report.call_args.kwargs["payload"]["winner_satellite_id"], "test_satellite_alpha")

    def test_arbitrate_provisional_capture_commits_on_proceed(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            wake_arbitration_timeout_seconds=0.75,
            wake_arbitration_loser_suppression_ms=1500,
        )
        runtime_state = types.SimpleNamespace(next_wake_time=0.0)
        decision = WakeArbitrationDecision(
            interaction_id="wake-1",
            satellite_id="test_satellite_alpha",
            winner_satellite_id="test_satellite_alpha",
            decision="proceed",
            reason="highest_audio_level",
            participants=["test_satellite_alpha"],
            window_ms=500,
        )

        with patch("pi_runtime.wake_arbitration.submit_wake_claim_for_decision", return_value=decision):
            result = arbitrate_provisional_capture(
                args=args,
                logger=logging.getLogger("satellite-wake-arbitration-test"),
                runtime_state=runtime_state,
                capture_func=lambda: _capture_outcome(b"committed-pcm"),
                satellite_id="test_satellite_alpha",
                wake_confidence=0.82,
                audio_level=0.41,
                correlation_id="corr-test-1",
            )

        self.assertTrue(result.proceeded)
        self.assertEqual(result.capture_outcome.pcm_bytes, b"committed-pcm")
        self.assertIs(result.decision, decision)
        self.assertEqual(runtime_state.wake_state, WAKE_STATE_ACTIVE_LISTENING)

    def test_arbitrate_provisional_capture_runs_commit_callback_only_on_proceed(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            wake_arbitration_timeout_seconds=0.75,
            wake_arbitration_loser_suppression_ms=1500,
        )
        runtime_state = types.SimpleNamespace(next_wake_time=0.0)
        decision = WakeArbitrationDecision(
            interaction_id="wake-1",
            satellite_id="test_satellite_alpha",
            winner_satellite_id="test_satellite_alpha",
            decision="proceed",
            reason="highest_audio_level",
            participants=["test_satellite_alpha"],
            window_ms=500,
        )
        committed = []

        with patch("pi_runtime.wake_arbitration.submit_wake_claim_for_decision", return_value=decision):
            arbitrate_provisional_capture(
                args=args,
                logger=logging.getLogger("satellite-wake-arbitration-test"),
                runtime_state=runtime_state,
                capture_func=lambda: _capture_outcome(b"committed-pcm"),
                satellite_id="test_satellite_alpha",
                wake_confidence=0.82,
                audio_level=0.41,
                correlation_id="corr-test-1",
                on_proceed=lambda: committed.append(True),
            )

        self.assertEqual(committed, [True])

    def test_arbitrate_provisional_capture_discards_on_stand_down(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_bravo",
            wake_arbitration_timeout_seconds=0.75,
            wake_arbitration_loser_suppression_ms=1500,
        )
        runtime_state = types.SimpleNamespace(next_wake_time=0.0)
        decision = WakeArbitrationDecision(
            interaction_id="wake-1",
            satellite_id="test_satellite_bravo",
            winner_satellite_id="test_satellite_alpha",
            decision="stand_down",
            reason="highest_audio_level",
            participants=["test_satellite_alpha", "test_satellite_bravo"],
            window_ms=500,
        )

        with patch("pi_runtime.wake_arbitration.submit_wake_claim_for_decision", return_value=decision):
            with patch("pi_runtime.wake_arbitration.report_satellite_activity"):
                committed = []
                result = arbitrate_provisional_capture(
                    args=args,
                    logger=logging.getLogger("satellite-wake-arbitration-test"),
                    runtime_state=runtime_state,
                    capture_func=lambda: _capture_outcome(b"discarded-pcm"),
                    satellite_id="test_satellite_bravo",
                    wake_confidence=0.72,
                    audio_level=0.2,
                    correlation_id="corr-test-1",
                    on_proceed=lambda: committed.append(True),
                )

        self.assertFalse(result.proceeded)
        self.assertIsNone(result.capture_outcome)
        self.assertIs(result.decision, decision)
        self.assertEqual(runtime_state.wake_state, WAKE_STATE_SUPPRESSED)
        self.assertEqual(committed, [])

    def test_arbitrate_provisional_capture_fails_open_when_claim_fails(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            wake_arbitration_timeout_seconds=0.75,
            wake_arbitration_loser_suppression_ms=1500,
        )
        runtime_state = types.SimpleNamespace(next_wake_time=0.0)

        with patch(
            "pi_runtime.wake_arbitration.submit_wake_claim_for_decision",
            side_effect=RuntimeError("brain unavailable"),
        ):
            result = arbitrate_provisional_capture(
                args=args,
                logger=logging.getLogger("satellite-wake-arbitration-test"),
                runtime_state=runtime_state,
                capture_func=lambda: _capture_outcome(b"fail-open-pcm"),
                satellite_id="test_satellite_alpha",
                wake_confidence=0.82,
                audio_level=0.41,
                correlation_id="corr-test-1",
            )

        self.assertTrue(result.proceeded)
        self.assertIsNone(result.decision)
        self.assertEqual(result.capture_outcome.pcm_bytes, b"fail-open-pcm")
        self.assertEqual(runtime_state.wake_state, WAKE_STATE_ACTIVE_LISTENING)


if __name__ == "__main__":
    unittest.main()
