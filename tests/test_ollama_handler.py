from __future__ import annotations

import sys
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.handlers.fallback_router import (
    FallbackRouterHandler,
    attempt_fallback_router_warmup,
    parse_fallback_router_decision,
)
from oracle_app.inference import InferenceClient, InferenceExecutionSettings, InferenceProviderSettings
from oracle_app.inference_bridges import InferenceBridgeError, InferenceProviderResponse
from oracle_app.music_runtime.ollama import parse_ollama_decision
from oracle_app.schemas import DispatchPlan


class _StubRegistry:
    def __init__(self, response: DispatchPlan) -> None:
        self.response = response
        self.calls: list[DispatchPlan] = []

    def execute(self, dispatch: DispatchPlan) -> DispatchPlan:
        self.calls.append(dispatch)
        return self.response


def _inference() -> InferenceClient:
    return InferenceClient(
        InferenceExecutionSettings(
            enabled=True,
            base_url="http://127.0.0.1:11434",
            model="phi4-mini:latest",
            timeout_seconds=20,
            keep_alive=-1,
            options={"temperature": 0.1},
            fallback_model="phi4-mini:latest",
            fallback_timeout_seconds=8,
        )
    )


def _fallback_inference() -> InferenceClient:
    provider = InferenceProviderSettings(
        provider_type="ollama",
        enabled=True,
        base_url="http://127.0.0.1:11434",
        model="phi4-mini:latest",
        timeout_seconds=8,
        keep_alive=-1,
        options={"temperature": 0.1},
    )
    return InferenceClient(
        InferenceExecutionSettings(
            enabled=True,
            base_url=provider.base_url,
            model=provider.model,
            timeout_seconds=provider.timeout_seconds,
            keep_alive=provider.keep_alive,
            options=provider.options,
            fallback_model=provider.model,
            fallback_timeout_seconds=provider.timeout_seconds,
            local_provider_id="local_ollama",
            providers={"local_ollama": provider},
            consumer_orders={"fallback_router": ("local_ollama",)},
            consumer_total_timeout_seconds={"fallback_router": 12},
        )
    )


class OllamaBridgeTests(unittest.TestCase):
    @patch("oracle_app.inference.warm_model")
    def test_shared_inference_warmup_preloads_without_generating_tokens(self, mock_warm_model) -> None:
        _inference().warm()

        mock_warm_model.assert_called_once_with(
            base_url="http://127.0.0.1:11434",
            model="phi4-mini:latest",
            timeout_seconds=20,
            keep_alive=-1,
        )

    @patch("oracle_app.inference.call_generate")
    def test_shared_inference_generate_uses_typed_settings(self, mock_call_generate) -> None:
        mock_call_generate.return_value = {
            "response": '{"mode":"answer","reply":"OK","command":"","reason":"test"}'
        }

        result = _inference().generate("hello", format="json")

        self.assertIn("response", result)
        mock_call_generate.assert_called_once()

    @patch("oracle_app.inference.call_generate")
    def test_shared_inference_propagates_non_timeout_failures(self, mock_call_generate) -> None:
        mock_call_generate.side_effect = socket.gaierror("lookup failed")

        with self.assertRaises(socket.gaierror):
            _inference().generate("hello")

        self.assertEqual(mock_call_generate.call_count, 1)

    def test_parse_json_wrapped_in_code_fence(self) -> None:
        decision = parse_ollama_decision(
            '```json\n{"mode":"answer","reply":"OK","command":"","reason":"small talk"}\n```'
        )
        self.assertEqual(decision["mode"], "answer")
        self.assertEqual(decision["reply"], "OK")

    def test_parse_json_embedded_in_extra_text(self) -> None:
        decision = parse_ollama_decision(
            'Here is the result: {"mode":"home_assistant","reply":"","command":"turn off the office lamp","reason":"clear device request"}'
        )
        self.assertEqual(decision["mode"], "home_assistant")
        self.assertEqual(decision["command"], "turn off the office lamp")

    def test_parse_calendar_mode(self) -> None:
        decision = parse_ollama_decision(
            '{"mode":"calendar","reply":"","command":"what is on my calendar tomorrow","reason":"calendar query"}'
        )
        self.assertEqual(decision["mode"], "calendar")
        self.assertEqual(decision["command"], "what is on my calendar tomorrow")

    def test_parse_audiobook_mode(self) -> None:
        decision = parse_ollama_decision(
            '{"mode":"audiobook","reply":"","command":"play audiobook dune","reason":"audiobook request"}'
        )
        self.assertEqual(decision["mode"], "audiobook")
        self.assertEqual(decision["command"], "play audiobook dune")

    def test_parse_fallback_router_decision_requires_domain_and_normalized_text(self) -> None:
        decision = parse_fallback_router_decision(
            '{"status":"resolved","domain":"music","normalized_text":"play david bowie","user_id":""}'
        )

        self.assertEqual(decision, {"status": "resolved", "domain": "music", "normalized_text": "play david bowie", "user_id": ""})
        self.assertEqual(
            parse_fallback_router_decision('{"status":"unsupported","domain":"","normalized_text":"","user_id":""}'),
            {"status": "unsupported", "domain": "", "normalized_text": "", "user_id": ""},
        )
        self.assertIsNone(parse_fallback_router_decision('{"status":"resolved","domain":"music","user_id":""}'))
        self.assertIsNone(parse_fallback_router_decision('{"status":"resolved","domain":"invalid","normalized_text":"hi","user_id":""}'))
        self.assertIsNone(parse_fallback_router_decision('{"status":"unresolved","domain":"facts","normalized_text":"hi","user_id":""}'))

    @patch("oracle_app.handlers.fallback_router.warm_fallback_router_model")
    def test_attempt_fallback_router_warmup_calls_warmup(self, mock_warm) -> None:
        inference = _inference()
        attempt_fallback_router_warmup(inference)

        mock_warm.assert_called_once_with(inference)

    def test_fallback_router_handler_returns_domain_proposal(self) -> None:
        inference = _fallback_inference()
        with patch.object(
            inference._providers["local_ollama"],  # noqa: SLF001 - provider-boundary fixture
            "generate",
            return_value=InferenceProviderResponse(
                '{"status":"resolved","domain":"facts","normalized_text":"explain black holes","user_id":""}',
                "phi4-mini:latest",
            ),
        ):
            dispatch = DispatchPlan(
                target="fallback_router",
                hook="fallback_router.decide",
                payload={"prompt": "explain black holes", "source": "test", "session_id": "session-1"},
                status="planned",
            )
            result = FallbackRouterHandler(inference).handle(dispatch, _StubRegistry(dispatch))

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["proposed_domain"], "facts")
        self.assertEqual(result.result["semantic_status"], "resolved")
        self.assertEqual(result.result["inference"]["provider_id"], "local_ollama")

    def test_fallback_router_handler_returns_terminal_unsupported(self) -> None:
        inference = _fallback_inference()
        with patch.object(
            inference._providers["local_ollama"],  # noqa: SLF001 - provider-boundary fixture
            "generate",
            return_value=InferenceProviderResponse(
                '{"status":"unsupported","domain":"","normalized_text":"","user_id":""}',
                "phi4-mini:latest",
            ),
        ):
            dispatch = DispatchPlan(
                target="fallback_router",
                hook="fallback_router.decide",
                payload={"prompt": "tell me a joke", "source": "test", "session_id": "session-2"},
                status="planned",
            )
            result = FallbackRouterHandler(inference).handle(dispatch, _StubRegistry(dispatch))

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["semantic_status"], "unsupported")
        self.assertEqual(result.result["proposed_domain"], "")

    def test_fallback_router_handler_fails_invalid_output(self) -> None:
        inference = _fallback_inference()
        with patch.object(
            inference._providers["local_ollama"],  # noqa: SLF001 - provider-boundary fixture
            "generate",
            return_value=InferenceProviderResponse('{"domain":"music"}', "phi4-mini:latest"),
        ):
            dispatch = DispatchPlan(
                target="fallback_router",
                hook="fallback_router.decide",
                payload={"prompt": "play bowie", "source": "test", "session_id": "session-3"},
                status="planned",
            )
            result = FallbackRouterHandler(inference).handle(dispatch, _StubRegistry(dispatch))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "fallback_router_providers_exhausted")
        self.assertEqual(result.result["attempts"][0]["outcome"], "contract_failure")

    def test_fallback_router_handler_fails_timeout(self) -> None:
        inference = _fallback_inference()
        with patch.object(
            inference._providers["local_ollama"],  # noqa: SLF001 - provider-boundary fixture
            "generate",
            side_effect=InferenceBridgeError("timeout", "timed out"),
        ):
            dispatch = DispatchPlan(
                target="fallback_router",
                hook="fallback_router.decide",
                payload={"prompt": "tell me a joke", "source": "test", "session_id": "session-timeout"},
                status="planned",
            )
            result = FallbackRouterHandler(inference).handle(dispatch, _StubRegistry(dispatch))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "fallback_router_providers_exhausted")
        self.assertEqual(result.result["attempts"][0]["detail_code"], "timeout")

    def test_fallback_router_handler_rejects_lost_temporal_value(self) -> None:
        inference = _fallback_inference()
        with patch.object(
            inference._providers["local_ollama"],  # noqa: SLF001 - provider-boundary fixture
            "generate",
            return_value=InferenceProviderResponse(
                '{"status":"resolved","domain":"calendar","normalized_text":"calendar today","user_id":""}',
                "phi4-mini:latest",
            ),
        ):
            dispatch = DispatchPlan(
                target="fallback_router",
                hook="fallback_router.decide",
                payload={"prompt": "what is on my calendar tomorrow", "source": "test", "session_id": "session-4"},
                status="planned",
            )
            result = FallbackRouterHandler(inference).handle(dispatch, _StubRegistry(dispatch))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["attempts"][0]["outcome"], "contract_failure")

if __name__ == "__main__":
    unittest.main()
