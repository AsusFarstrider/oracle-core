from __future__ import annotations

import json
import logging
import queue
import sys
import types
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))

numpy_module = types.ModuleType("numpy")


class _DummyArray:
    def __init__(self, size: int) -> None:
        self.size = size

    def copy(self):
        return self

    def astype(self, _dtype):
        return self

    def __truediv__(self, _other):
        return self

    def __mul__(self, _other):
        return self


def _dummy_frombuffer(payload: bytes, dtype=None):
    return _DummyArray(len(payload) // 2)


numpy_module.frombuffer = _dummy_frombuffer
numpy_module.int16 = "int16"
numpy_module.float32 = "float32"
numpy_module.sqrt = lambda value: value
numpy_module.mean = lambda value: 0.0
sys.modules.setdefault("numpy", numpy_module)
requests_module = types.ModuleType("requests")
requests_module.post = lambda *args, **kwargs: None
requests_module.get = lambda *args, **kwargs: None
requests_module.RequestException = RuntimeError
sys.modules.setdefault("requests", requests_module)
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
openwakeword_module = types.ModuleType("openwakeword")
openwakeword_model_module = types.ModuleType("openwakeword.model")
openwakeword_model_module.Model = object
openwakeword_module.model = openwakeword_model_module
sys.modules.setdefault("openwakeword", openwakeword_module)
sys.modules.setdefault("openwakeword.model", openwakeword_model_module)

import pi_runtime.oracle_client as oracle_client
import pi_runtime.request_runtime as request_runtime
from pi_runtime.reply_runtime import ReplyRuntime
from pi_runtime.models import CommandOutcome


class _FakeHttpResponse:
    status = 200

    def read(self) -> bytes:
        return json.dumps({"text": "turn on lights"}).encode("utf-8")


class _FakeHttpConnection:
    last: "_FakeHttpConnection | None" = None

    def __init__(self, host: str | None, port: int | None, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.headers: dict[str, str] = {}
        self.body = b""
        self.path = ""
        _FakeHttpConnection.last = self

    def putrequest(self, method: str, path: str) -> None:
        self.method = method
        self.path = path

    def putheader(self, name: str, value: str) -> None:
        self.headers[name] = value

    def endheaders(self) -> None:
        pass

    def send(self, body: bytes) -> None:
        self.body += body

    def getresponse(self) -> _FakeHttpResponse:
        return _FakeHttpResponse()

    def close(self) -> None:
        pass


class SatelliteCorrelationPropagationTests(unittest.TestCase):
    def test_send_stt_sends_correlation_header_and_source_metadata(self) -> None:
        with patch("pi_runtime.oracle_client.http.client.HTTPConnection", _FakeHttpConnection):
            transcript = oracle_client.send_stt(
                "http://oracle",
                b"wav-bytes",
                correlation_id="corr-test-1",
                source="test_satellite_alpha",
                credential="brain-token",
            )

        self.assertEqual(transcript, "turn on lights")
        connection = _FakeHttpConnection.last
        self.assertIsNotNone(connection)
        assert connection is not None
        self.assertEqual(connection.path, "/api/speech/stt")
        self.assertEqual(connection.headers["X-Oracle-Correlation-Id"], "corr-test-1")
        self.assertEqual(connection.headers["Authorization"], "Bearer brain-token")
        self.assertIn(b'name="source"', connection.body)
        self.assertIn(b"test_satellite_alpha", connection.body)
        self.assertIn(b'name="audio"; filename="speech.wav"', connection.body)
        self.assertIn(b"wav-bytes", connection.body)

    def test_send_stt_remains_audio_only_when_metadata_is_absent(self) -> None:
        with patch("pi_runtime.oracle_client.http.client.HTTPConnection", _FakeHttpConnection):
            transcript = oracle_client.send_stt("http://oracle", b"wav-bytes")

        self.assertEqual(transcript, "turn on lights")
        connection = _FakeHttpConnection.last
        self.assertIsNotNone(connection)
        assert connection is not None
        self.assertNotIn("X-Oracle-Correlation-Id", connection.headers)
        self.assertNotIn("Authorization", connection.headers)
        self.assertNotIn(b'name="source"', connection.body)
        self.assertIn(b'name="audio"; filename="speech.wav"', connection.body)

    def test_send_command_uses_correlation_header_without_changing_json_body(self) -> None:
        fake_response = Mock()
        fake_response.json.return_value = {
            "reply_text": "done", "status": "executed", "failure_code": None,
            "effects": {"follow_up": None, "satellite_playback": None, "deferred_satellite_playback": None, "ui_presentation": None}, "source_id": "test_satellite_alpha",
            "session_id": "test_satellite_alpha-session-1", "trace_id": "trace-1",
        }
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True) as mock_post:
            outcome = oracle_client.send_command(
                "http://oracle",
                "test_satellite_alpha",
                "turn on lights",
                "test_satellite_alpha-session-1",
                correlation_id="corr-test-1",
                credential="brain-token",
            )

        self.assertEqual(outcome.spoken_reply, "done")
        mock_post.assert_called_once_with(
            "http://oracle/api/conversation/command",
            json={"text": "turn on lights", "source": "test_satellite_alpha", "session_id": "test_satellite_alpha-session-1"},
            timeout=120,
            headers={
                "Authorization": "Bearer brain-token",
                "X-Oracle-Correlation-Id": "corr-test-1",
            },
        )

    def test_send_command_omits_header_when_correlation_is_absent(self) -> None:
        fake_response = Mock()
        fake_response.json.return_value = {
            "reply_text": "done", "status": "executed", "failure_code": None,
            "effects": {"follow_up": None, "satellite_playback": None, "deferred_satellite_playback": None, "ui_presentation": None}, "source_id": "test_satellite_alpha",
            "session_id": "test_satellite_alpha-session-1", "trace_id": "trace-1",
        }
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True) as mock_post:
            oracle_client.send_command(
                "http://oracle",
                "test_satellite_alpha",
                "turn on lights",
                "test_satellite_alpha-session-1",
            )

        mock_post.assert_called_once_with(
            "http://oracle/api/conversation/command",
            json={"text": "turn on lights", "source": "test_satellite_alpha", "session_id": "test_satellite_alpha-session-1"},
            timeout=120,
        )

    def test_deferred_resume_posts_opaque_continuation(self) -> None:
        fake_response = Mock()
        fake_response.json.return_value = {"ok": True}
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True) as mock_post:
            result = oracle_client.resume_deferred_playback(
                "http://oracle",
                "test_satellite_desktop",
                "opaque-token",
                credential="brain-token",
            )

        self.assertEqual(result, {"ok": True})
        fake_response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            "http://oracle/api/satellite/deferred-resume",
            json={
                "source": "test_satellite_desktop",
                "continuation_token": "opaque-token",
            },
            timeout=120,
            headers={"Authorization": "Bearer brain-token"},
        )

    def test_submit_wake_claim_posts_metadata_and_correlation_header(self) -> None:
        fake_response = Mock()
        fake_response.json.return_value = {
            "interaction_id": "wake-test-1",
            "satellite_id": "test_satellite_alpha",
            "winner_satellite_id": "test_satellite_alpha",
            "decision": "proceed",
            "reason": "highest_audio_level",
            "participants": ["test_satellite_alpha", "test_satellite_bravo"],
            "window_ms": 500,
            "room_id": "kitchen",
            "profile": "kitchen_touch_v1",
        }
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True) as mock_post:
            decision = oracle_client.submit_wake_claim(
                "http://oracle",
                satellite_id="test_satellite_alpha",
                room_id="kitchen",
                profile="kitchen_touch_v1",
                timestamp="2026-05-23T12:00:00Z",
                wake_confidence=0.82,
                audio_level=0.41,
                correlation_id="corr-test-1",
                credential="brain-token",
                timeout=0.75,
            )

        self.assertTrue(decision.should_proceed)
        self.assertEqual(decision.interaction_id, "wake-test-1")
        self.assertEqual(decision.winner_satellite_id, "test_satellite_alpha")
        self.assertEqual(decision.participants, ["test_satellite_alpha", "test_satellite_bravo"])
        self.assertEqual(decision.room_id, "kitchen")
        self.assertEqual(decision.profile, "kitchen_touch_v1")
        mock_post.assert_called_once_with(
            "http://oracle/api/satellite/wake",
            json={
                "satellite_id": "test_satellite_alpha",
                "room_id": "kitchen",
                "profile": "kitchen_touch_v1",
                "timestamp": "2026-05-23T12:00:00Z",
                "wake_confidence": 0.82,
                "audio_level": 0.41,
                "correlation_id": "corr-test-1",
            },
            timeout=0.75,
            headers={
                "Authorization": "Bearer brain-token",
                "X-Oracle-Correlation-Id": "corr-test-1",
            },
        )

    def test_submit_wake_claim_parses_stand_down_response(self) -> None:
        fake_response = Mock()
        fake_response.json.return_value = {
            "interaction_id": "wake-test-1",
            "satellite_id": "test_satellite_bravo",
            "winner_satellite_id": "test_satellite_alpha",
            "decision": "stand_down",
            "reason": "highest_audio_level",
            "participants": ["test_satellite_alpha", "test_satellite_bravo"],
            "window_ms": 500,
        }
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True):
            decision = oracle_client.submit_wake_claim(
                "http://oracle",
                satellite_id="test_satellite_bravo",
                wake_confidence=0.71,
                audio_level=0.2,
            )

        self.assertFalse(decision.should_proceed)
        self.assertEqual(decision.decision, "stand_down")
        self.assertEqual(decision.satellite_id, "test_satellite_bravo")
        self.assertEqual(decision.winner_satellite_id, "test_satellite_alpha")

    def test_report_satellite_activity_posts_structured_payload(self) -> None:
        fake_response = Mock()
        with patch("pi_runtime.oracle_client.requests.post", return_value=fake_response, create=True) as mock_post:
            oracle_client.report_satellite_activity(
                "http://oracle",
                source_id="test_satellite_alpha",
                event_type="wake_detected",
                status="available",
                correlation_id="corr-test-1",
                credential="brain-token",
                payload={"wake_score": 0.9},
                snapshot={"last_wake_at": "2026-04-28T12:00:00+00:00"},
                timeout=0.2,
            )

        mock_post.assert_called_once_with(
            "http://oracle/api/satellite/activity",
            json={
                "source_id": "test_satellite_alpha",
                "payload": {"wake_score": 0.9},
                "snapshot": {"last_wake_at": "2026-04-28T12:00:00+00:00"},
                "event_type": "wake_detected",
                "status": "available",
                "correlation_id": "corr-test-1",
            },
            headers={
                "Authorization": "Bearer brain-token",
                "X-Oracle-Correlation-Id": "corr-test-1",
            },
            timeout=0.2,
        )

    def test_report_satellite_activity_fails_open(self) -> None:
        with patch(
            "pi_runtime.oracle_client.requests.post",
            side_effect=RuntimeError("brain offline"),
            create=True,
        ):
            oracle_client.report_satellite_activity(
                "http://oracle",
                source_id="test_satellite_alpha",
                event_type="wake_detected",
            )

    def test_authenticated_alert_and_command_event_reads_attach_bearer_header(self) -> None:
        alert_response = Mock()
        alert_response.json.return_value = {"alerts": [{"alert_id": "timer-1"}]}
        event_response = Mock()
        event_response.json.return_value = {"events": [{"event_id": 7}]}
        with patch(
            "pi_runtime.oracle_client.requests.post",
            return_value=alert_response,
            create=True,
        ) as mock_post, patch(
            "pi_runtime.oracle_client.requests.get", return_value=event_response, create=True,
        ) as mock_get:
            alerts = oracle_client.claim_due_alerts(
                "http://oracle",
                "test_satellite_alpha",
                credential="brain-token",
            )
            events = oracle_client.fetch_command_events(
                "http://oracle",
                source="test_satellite_alpha",
                session_id="session-1",
                after_event_id=6,
                timeout=0.75,
                credential="brain-token",
            )

        self.assertEqual(alerts, [{"alert_id": "timer-1"}])
        self.assertEqual(events, [{"event_id": 7}])
        self.assertEqual(
            mock_post.call_args,
            call(
                "http://oracle/api/satellite/alerts/claim",
                json={"source_id": "test_satellite_alpha", "lease_seconds": 60, "limit": 16},
                timeout=30,
                headers={"Authorization": "Bearer brain-token"},
            ),
        )
        self.assertEqual(
            mock_get.call_args,
            call(
                "http://oracle/api/conversation/command-events",
                params={
                    "source": "test_satellite_alpha",
                    "session_id": "session-1",
                    "after_event_id": 6,
                },
                timeout=0.75,
                headers={"Authorization": "Bearer brain-token"},
            ),
        )

    def test_authenticated_tts_attaches_bearer_header(self) -> None:
        response = Mock(content=b"tts-wav")
        with patch(
            "pi_runtime.oracle_client.requests.post",
            return_value=response,
            create=True,
        ) as mock_post:
            payload = oracle_client.request_tts(
                "http://oracle",
                "hello",
                credential="brain-token",
            )

        self.assertEqual(payload, b"tts-wav")
        mock_post.assert_called_once_with(
            "http://oracle/api/speech/tts",
            json={"text": "hello"},
            timeout=120,
            headers={"Authorization": "Bearer brain-token"},
        )

    @patch("pi_runtime.request_runtime.request_tts", return_value=b"")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="turn on lights")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("test_satellite_alpha-session-1", 123.0))
    @patch("pi_runtime.request_runtime.pcm_to_wav_bytes", return_value=b"wav-bytes")
    def test_request_pipeline_sends_same_correlation_to_stt_and_command(
        self,
        _mock_wav,
        _mock_session,
        mock_send_stt,
        mock_send_command,
        _mock_tts,
    ) -> None:
        mock_send_command.return_value = CommandOutcome(
            transcript="turn on lights",
            spoken_reply="",
            raw_response={"route": {"target": "system"}, "dispatch": {"status": "executed"}},
        )
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)

        request_runtime.run_request_pipeline(
            args=args,
            logger=logging.getLogger("satellite-correlation-test"),
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
            correlation_id="corr-test-1",
        )

        self.assertEqual(mock_send_stt.call_args.kwargs["correlation_id"], "corr-test-1")
        self.assertEqual(mock_send_stt.call_args.kwargs["source"], "test_satellite_alpha")
        self.assertEqual(mock_send_stt.call_args.kwargs["credential"], "brain-token")
        self.assertEqual(mock_send_command.call_args.kwargs["correlation_id"], "corr-test-1")
        self.assertEqual(mock_send_command.call_args.kwargs["credential"], "brain-token")

    @patch("pi_runtime.request_runtime.request_tts", return_value=b"")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="turn on lights")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("test_satellite_alpha-session-1", 123.0))
    @patch("pi_runtime.request_runtime.pcm_to_wav_bytes", return_value=b"wav-bytes")
    def test_request_pipeline_generates_one_matching_correlation_when_missing(
        self,
        _mock_wav,
        _mock_session,
        mock_send_stt,
        mock_send_command,
        _mock_tts,
    ) -> None:
        mock_send_command.return_value = CommandOutcome(
            transcript="turn on lights",
            spoken_reply="",
            raw_response={"route": {"target": "system"}, "dispatch": {"status": "executed"}},
        )
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)

        request_runtime.run_request_pipeline(
            args=args,
            logger=logging.getLogger("satellite-correlation-test"),
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
        )

        stt_correlation_id = mock_send_stt.call_args.kwargs["correlation_id"]
        command_correlation_id = mock_send_command.call_args.kwargs["correlation_id"]
        self.assertTrue(stt_correlation_id.startswith("corr_"))
        self.assertEqual(command_correlation_id, stt_correlation_id)

    @patch("pi_runtime.request_runtime.report_satellite_activity")
    @patch("pi_runtime.request_runtime.send_stt", side_effect=RuntimeError("stt offline"))
    @patch("pi_runtime.request_runtime.pcm_to_wav_bytes", return_value=b"wav-bytes")
    def test_request_pipeline_reports_stt_upload_failure_without_changing_error(
        self,
        _mock_wav,
        _mock_send_stt,
        mock_report_activity,
    ) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)

        with self.assertRaises(request_runtime.RequestPipelineError) as raised:
            request_runtime.run_request_pipeline(
                args=args,
                logger=logging.getLogger("satellite-correlation-test"),
                runtime_state=runtime_state,
                pcm_bytes=b"\x00\x00" * 64,
                correlation_id="corr-test-1",
            )

        self.assertEqual(raised.exception.kind, "stt_failed")
        mock_report_activity.assert_called_once()
        self.assertEqual(mock_report_activity.call_args.kwargs["event_type"], "stt_upload_failed")
        self.assertEqual(mock_report_activity.call_args.kwargs["correlation_id"], "corr-test-1")
        self.assertEqual(mock_report_activity.call_args.kwargs["credential"], "brain-token")

    @patch("pi_runtime.reply_runtime.report_satellite_activity")
    @patch("pi_runtime.reply_runtime.play_wav_bytes", side_effect=RuntimeError("speaker offline"))
    def test_reply_runtime_reports_tts_playback_failure_without_raising(
        self,
        _mock_play_wav,
        mock_report_activity,
    ) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            source="test_satellite_alpha",
            output_device_index=None,
            playback_gain=0.3,
            interrupt_replies=False,
            reply_audio_state_path="/tmp/oracle-reply-state.json",
            reply_audio_stop_path="/tmp/oracle-reply-stop",
        )
        runtime_state = types.SimpleNamespace(
            active_session_id="test_satellite_alpha-session-1",
            reply_output_handoff_until=0.0,
            next_wake_time=0.0,
        )
        reply_runtime = ReplyRuntime(
            args=args,
            logger=logging.getLogger("satellite-correlation-test"),
            frame_queue=queue.Queue(),
            pre_roll=deque(),
            wake_model=None,
            wake_key="",
            runtime_state=runtime_state,
        )

        result = reply_runtime.play_reply(
            tts_wav=b"wav",
            outcome=CommandOutcome(transcript="hello", spoken_reply="hello", raw_response={}),
            foreground_handoff=None,
            interrupted_playback=None,
            process_capture=lambda **kwargs: None,
            correlation_id="corr-test-1",
        )

        self.assertEqual(result.foreground_final_state, "failed")
        mock_report_activity.assert_called_once()
        self.assertEqual(mock_report_activity.call_args.kwargs["event_type"], "tts_playback_failed")
        self.assertEqual(mock_report_activity.call_args.kwargs["correlation_id"], "corr-test-1")
        self.assertEqual(mock_report_activity.call_args.kwargs["credential"], "brain-token")


if __name__ == "__main__":
    unittest.main()
