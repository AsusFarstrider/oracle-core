from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from stt import (
    DisabledSttProvider,
    FastWhisperProvider,
    WhisperCppProvider,
    SttResult,
    attempt_stt_provider_warmup,
)
from tts import DisabledTtsProvider, PiperTtsProvider, TtsResult
from fastapi import UploadFile

from oracle_app.configuration import (
    BrainCoreRuntimeConsumers,
    BrainRuntimeSettings,
    EffectiveConfig,
    inspect_candidate,
)
from oracle_app.handlers.fallback_router import attempt_fallback_router_warmup
from oracle_app.api import _synthesize_speech_with_provider, _transcribe_audio_with_provider
from oracle_app.dispatch import build_dispatch_registry, execute_dispatch
from oracle_app.schemas import DispatchPlan, TtsRequest
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
POLICY = retention_policy_from_configuration(MemoryRetentionConfiguration())


class BrainCoreRuntimeConsumersTests(unittest.TestCase):
    def test_disabled_brain_roles_construct_explicit_disabled_consumers(self) -> None:
        consumers = self._consumers()

        self.assertIsInstance(consumers.stt_provider, DisabledSttProvider)
        self.assertIsInstance(consumers.tts_provider, DisabledTtsProvider)
        self.assertFalse(consumers.inference.enabled)
        self.assertIsNone(consumers.inference.base_url)
        self.assertEqual(dict(consumers.inference.options), {})

    def test_enabled_typed_providers_map_without_legacy_configuration(self) -> None:
        with patch.object(FastWhisperProvider, "begin_warmup") as begin_warmup:
            consumers = self._consumers(mode="enabled")

        self.assertIsInstance(consumers.stt_provider, FastWhisperProvider)
        self.assertEqual(consumers.stt_provider.model, "small.en")
        self.assertEqual(consumers.stt_provider.threads, 8)
        self.assertIsInstance(consumers.tts_provider, PiperTtsProvider)
        self.assertEqual(consumers.tts_provider.binary, "bin/piper")
        self.assertEqual(consumers.tts_provider.model, "models/voice.onnx")
        self.assertTrue(consumers.inference.enabled)
        self.assertEqual(consumers.inference.base_url, "http://127.0.0.1:11434")
        self.assertEqual(consumers.inference.model, "example-model")
        self.assertEqual(consumers.inference.fallback_model, "routing-model")
        self.assertEqual(consumers.inference.fallback_timeout_seconds, 9.0)
        self.assertEqual(consumers.inference.options["seed"], 7)
        with self.assertRaises(TypeError):
            consumers.inference.options["seed"] = 8  # type: ignore[index]
        begin_warmup.assert_not_called()

    def test_whisper_cpp_maps_machine_paths_and_threads(self) -> None:
        consumers = self._consumers(mode="whisper_cpp")

        self.assertIsInstance(consumers.stt_provider, WhisperCppProvider)
        self.assertEqual(consumers.stt_provider.binary, "bin/whisper-cli")
        self.assertEqual(consumers.stt_provider.model, "models/whisper.bin")
        self.assertEqual(consumers.stt_provider.threads, 8)

    def test_explicit_canonical_warmups_do_not_read_legacy_settings(self) -> None:
        consumers = self._consumers(mode="enabled")

        with patch.object(FastWhisperProvider, "begin_warmup", return_value=True) as begin_warmup:
            attempt_stt_provider_warmup(consumers.stt_provider)
        with patch("oracle_app.inference.warm_model") as warm_model:
            attempt_fallback_router_warmup(consumers.inference)

        begin_warmup.assert_called_once_with(consumers.stt_provider)
        warm_model.assert_called_once_with(
            base_url="http://127.0.0.1:11434",
            model="routing-model",
            timeout_seconds=9.0,
            keep_alive=-1,
        )

    def test_explicit_voice_consumers_do_not_read_legacy_provider_getters(self) -> None:
        with patch.object(FastWhisperProvider, "begin_warmup"):
            consumers = self._consumers(mode="enabled")
        sample = tempfile.SpooledTemporaryFile()
        sample.write(b"wave")
        sample.seek(0)
        upload = UploadFile(filename="sample.wav", file=sample)

        with (
            patch.object(
                consumers.tts_provider,
                "synthesize",
                return_value=TtsResult(b"audio", "audio/wav", "piper"),
            ) as synthesize,
            patch.object(
                consumers.stt_provider,
                "transcribe",
                return_value=SttResult("hello oracle", "fast-whisper"),
            ) as transcribe,
            patch("oracle_app.api.safe_record_transcript"),
        ):
            response = _synthesize_speech_with_provider(
                TtsRequest(text="Hello"),
                consumers.tts_provider,
            )
            result = asyncio.run(
                _transcribe_audio_with_provider(
                    upload,
                    consumers.stt_provider,
                    source="example_source",
                    retention_policy=POLICY,
                )
            )

        self.assertEqual(response.body, b"audio")
        self.assertEqual(result.text, "hello oracle")
        synthesize.assert_called_once_with("Hello")
        transcribe.assert_called_once_with(b"wave", "sample.wav")

    def test_explicit_fallback_registry_does_not_read_legacy_settings(self) -> None:
        consumers = self._consumers(mode="enabled")
        registry = build_dispatch_registry(inference_client=consumers.inference)
        dispatch = DispatchPlan(
            target="fallback_router",
            hook="fallback_router.decide",
            payload={"prompt": "tell me a joke", "source": "test", "session_id": "session-1"},
            status="planned",
        )

        with patch("oracle_app.inference.call_generate") as generate:
            generate.return_value = {
                "response": '{"domain":"facts","normalized_text":"tell me a joke","user_id":""}'
            }
            result = execute_dispatch(dispatch, registry=registry)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["proposed_domain"], "facts")
        self.assertEqual(generate.call_args.kwargs["base_url"], "http://127.0.0.1:11434")
        self.assertEqual(generate.call_args.kwargs["model"], "routing-model")

    def test_explicit_disabled_fallback_fails_without_legacy_fallback(self) -> None:
        consumers = self._consumers()
        registry = build_dispatch_registry(inference_client=consumers.inference)
        dispatch = DispatchPlan(
            target="fallback_router",
            hook="fallback_router.decide",
            payload={"prompt": "tell me a joke", "source": "test", "session_id": "session-1"},
            status="planned",
        )

        result = execute_dispatch(dispatch, registry=registry)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "fallback_router_disabled")

    def _consumers(self, *, mode: str | None = None) -> BrainCoreRuntimeConsumers:
        effective = self._effective_config(mode=mode)
        return BrainCoreRuntimeConsumers.from_runtime_settings(
            BrainRuntimeSettings.from_effective_config(effective)
        )

    def _effective_config(self, *, mode: str | None = None) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if mode is not None:
                path = bundle / "brain.yaml"
                brain = __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
                brain["speech"]["stt"]["enabled"] = True
                brain["speech"]["stt"]["provider"] = (
                    "local_whisper_cpp" if mode == "whisper_cpp" else "local_fast_whisper"
                )
                if mode == "enabled":
                    brain["speech"]["tts"]["enabled"] = True
                    brain["speech"]["tts"]["provider"] = "local_piper"
                    brain["inference"]["shared_backend"]["enabled"] = True
                    brain["inference"]["shared_backend"]["provider"] = "local_ollama"
                    brain["inference"]["shared_backend"]["fallback_router"] = {
                        "model": "routing-model",
                        "timeout_seconds": 9,
                    }
                path.write_text(json.dumps(brain), encoding="utf-8")
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
