from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import Mock, patch

from oracle_app.inference import (
    InferenceClient,
    InferenceContractError,
    InferenceExecutionError,
    InferenceExecutionSettings,
    InferenceProviderSettings,
)
from oracle_app.inference_bridges import (
    InferenceBridgeError,
    InferenceProviderResponse,
    OllamaInferenceBridge,
    OpenAILunaInferenceBridge,
)
from oracle_app.health import check_inference_health


SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


class _HttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class InferenceProviderTranslationTests(unittest.TestCase):
    @patch("oracle_app.inference_bridges.request.urlopen")
    def test_luna_uses_bounded_responses_request_without_storage_or_tools(self, urlopen) -> None:
        urlopen.return_value = _HttpResponse(
            {
                "id": "resp_fixture",
                "status": "completed",
                "model": "gpt-5.6-luna",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"status":"unresolved"}'}],
                    }
                ],
            }
        )
        bridge = OpenAILunaInferenceBridge(
            base_url="https://api.openai.com",
            model="gpt-5.6-luna",
            api_key="fixture-secret",
            max_output_tokens=256,
        )

        response = bridge.generate(
            prompt="ambiguous request",
            system="Return the contract.",
            json_schema=SCHEMA,
            timeout_seconds=4.0,
        )

        req = urlopen.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(req.get_header("Authorization"), "Bearer fixture-secret")
        self.assertEqual(payload["model"], "gpt-5.6-luna")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["max_output_tokens"], 256)
        self.assertEqual(payload["text"]["format"]["schema"], SCHEMA)
        self.assertNotIn("tools", payload)
        self.assertEqual(response.text, '{"status":"unresolved"}')
        self.assertEqual(response.model, "gpt-5.6-luna")
        self.assertEqual(response.request_id, "resp_fixture")
        self.assertNotIn("fixture-secret", repr(bridge))

    @patch("oracle_app.inference_bridges.request.urlopen")
    def test_ollama_translation_makes_one_attempt_and_reports_returned_model(self, urlopen) -> None:
        urlopen.return_value = _HttpResponse(
            {"response": '{"status":"resolved"}', "model": "phi4-mini:latest"}
        )
        bridge = OllamaInferenceBridge(
            base_url="http://127.0.0.1:11434/",
            model="phi4-mini:latest",
            keep_alive=-1,
            options={"temperature": 0.1},
        )

        response = bridge.generate(
            prompt="play bowie",
            system=None,
            json_schema=SCHEMA,
            timeout_seconds=3.0,
        )

        req = urlopen.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(payload["format"], "json")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(response.model, "phi4-mini:latest")

    @patch("oracle_app.inference_bridges.request.urlopen", side_effect=socket.timeout())
    def test_provider_timeout_is_normalized_without_response_body_or_secret(self, urlopen) -> None:
        bridge = OpenAILunaInferenceBridge(
            base_url="https://api.openai.com",
            model="gpt-5.6-luna",
            api_key="fixture-secret",
            max_output_tokens=256,
        )

        with self.assertRaises(InferenceBridgeError) as caught:
            bridge.generate(prompt="hello", system=None, json_schema=SCHEMA, timeout_seconds=2.0)

        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertEqual(urlopen.call_count, 1)


class InferenceFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.client = InferenceClient(
            InferenceExecutionSettings(
                enabled=True,
                base_url="http://127.0.0.1:11434",
                model="phi4-mini:latest",
                timeout_seconds=10,
                keep_alive=-1,
                options={},
                fallback_model="phi4-mini:latest",
                fallback_timeout_seconds=8,
                providers={
                    "luna": InferenceProviderSettings(
                        provider_type="openai_luna",
                        enabled=True,
                        base_url="https://api.openai.com",
                        model="gpt-5.6-luna",
                        timeout_seconds=5,
                        api_key="fixture-secret",
                    ),
                    "ollama": InferenceProviderSettings(
                        provider_type="ollama",
                        enabled=True,
                        base_url="http://127.0.0.1:11434",
                        model="phi4-mini:latest",
                        timeout_seconds=5,
                    ),
                },
                consumer_orders={
                    "fallback_router": ("luna", "ollama"),
                    "facts_summarizer": ("ollama", "luna"),
                },
                consumer_total_timeout_seconds={
                    "fallback_router": 8,
                    "facts_summarizer": 8,
                },
                unhealthy_cooldown_seconds=30,
            ),
            clock=lambda: self.now,
        )

    def test_operational_and_contract_failure_advance_to_secondary(self) -> None:
        luna = Mock()
        luna.generate.side_effect = InferenceBridgeError("timeout", "timed out")
        ollama = Mock()
        ollama.generate.return_value = InferenceProviderResponse('{"status":"resolved"}', "phi4-mini:latest")
        self.client._providers = {"luna": luna, "ollama": ollama}  # noqa: SLF001

        result = self.client.execute(
            "fallback_router",
            prompt="play bowie",
            system=None,
            json_schema=SCHEMA,
            validate=json.loads,
        )

        self.assertEqual(result.provider_id, "ollama")
        self.assertEqual([item.outcome for item in result.attempts], ["operational_failure", "success"])

        self.now += 31
        luna.generate.side_effect = None
        luna.generate.return_value = InferenceProviderResponse("not-json", "gpt-5.6-luna")

        def validate(value: str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise InferenceContractError("invalid") from exc

        result = self.client.execute(
            "fallback_router", prompt="play bowie", system=None, json_schema=SCHEMA, validate=validate
        )
        self.assertEqual(result.provider_id, "ollama")
        self.assertEqual([item.outcome for item in result.attempts], ["contract_failure", "success"])

    def test_valid_unresolved_is_terminal_and_does_not_ask_secondary(self) -> None:
        luna = Mock()
        luna.generate.return_value = InferenceProviderResponse('{"status":"unresolved"}', "gpt-5.6-luna")
        ollama = Mock()
        self.client._providers = {"luna": luna, "ollama": ollama}  # noqa: SLF001

        result = self.client.execute(
            "fallback_router",
            prompt="something ambiguous",
            system=None,
            json_schema=SCHEMA,
            validate=json.loads,
        )

        self.assertEqual(result.value, {"status": "unresolved"})
        self.assertEqual(len(result.attempts), 1)
        ollama.generate.assert_not_called()

    def test_unhealthy_provider_is_bypassed_then_recovers_after_cooldown(self) -> None:
        luna = Mock()
        luna.generate.side_effect = InferenceBridgeError("timeout", "timed out")
        ollama = Mock()
        ollama.generate.return_value = InferenceProviderResponse('{"status":"resolved"}', "phi4-mini:latest")
        self.client._providers = {"luna": luna, "ollama": ollama}  # noqa: SLF001
        arguments = dict(prompt="play bowie", system=None, json_schema=SCHEMA, validate=json.loads)

        self.client.execute("fallback_router", **arguments)
        degraded = check_inference_health(inference=self.client)
        self.assertEqual(degraded.status, "degraded")
        self.assertNotIn("fixture-secret", degraded.model_dump_json())
        second = self.client.execute("fallback_router", **arguments)
        self.assertEqual([item.outcome for item in second.attempts], ["bypassed", "success"])
        self.assertEqual(luna.generate.call_count, 1)

        self.now += 31
        luna.generate.side_effect = None
        luna.generate.return_value = InferenceProviderResponse('{"status":"resolved"}', "gpt-5.6-luna")
        recovered = self.client.execute("fallback_router", **arguments)
        self.assertEqual(recovered.provider_id, "luna")
        self.assertEqual(luna.generate.call_count, 2)
        self.assertEqual(check_inference_health(inference=self.client).status, "ok")

    def test_disabled_consumer_makes_zero_provider_calls(self) -> None:
        disabled = InferenceClient(
            InferenceExecutionSettings(
                enabled=True,
                base_url=None,
                model=None,
                timeout_seconds=None,
                keep_alive=None,
                options={},
                fallback_model=None,
                fallback_timeout_seconds=None,
                providers=self.client.settings.providers,
                consumer_orders={},
                consumer_total_timeout_seconds={},
            )
        )
        for bridge in disabled._providers.values():  # noqa: SLF001
            bridge.generate = Mock()  # type: ignore[method-assign]

        with self.assertRaises(InferenceExecutionError) as caught:
            disabled.execute(
                "fallback_router", prompt="hello", system=None, json_schema=SCHEMA, validate=json.loads
            )

        self.assertEqual(caught.exception.code, "consumer_disabled")
        for bridge in disabled._providers.values():  # noqa: SLF001
            bridge.generate.assert_not_called()  # type: ignore[attr-defined]
        self.assertEqual(check_inference_health(inference=disabled).status, "disabled")

    @patch("oracle_app.inference.call_generate")
    def test_legacy_music_compatible_generate_never_selects_luna(self, generate) -> None:
        generate.return_value = {"response": "{}"}

        self.client.generate("music request")

        self.assertEqual(generate.call_args.kwargs["base_url"], "http://127.0.0.1:11434")
        self.assertEqual(generate.call_args.kwargs["model"], "phi4-mini:latest")


if __name__ == "__main__":
    unittest.main()
