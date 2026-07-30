from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from stt import FastWhisperProvider, WhisperCppProvider, attempt_stt_warmup, build_stt_provider


class SttProviderTests(unittest.TestCase):
    def test_whisper_cpp_default_binary_is_portable_name(self) -> None:
        provider = build_stt_provider({"stt_provider": "whisper.cpp"})

        self.assertEqual(provider.binary, "whisper-cli")

    def test_build_whisper_cpp_provider(self) -> None:
        provider = build_stt_provider(
            {
                "stt_provider": "whisper.cpp",
                "stt_whisper_binary": "/tmp/whisper-cli",
                "stt_whisper_model": "/tmp/ggml-small.en.bin",
                "stt_whisper_threads": 6,
            }
        )

        self.assertIsInstance(provider, WhisperCppProvider)
        self.assertEqual(provider.binary, "/tmp/whisper-cli")
        self.assertEqual(provider.model, "/tmp/ggml-small.en.bin")
        self.assertEqual(provider.threads, 6)

    def test_build_fast_whisper_provider_reuses_existing_model_setting(self) -> None:
        provider = build_stt_provider(
            {
                "stt_provider": "fast-whisper",
                "stt_whisper_model": "/tmp/ggml-small.en.bin",
                "stt_whisper_threads": 8,
            }
        )

        self.assertIsInstance(provider, FastWhisperProvider)
        self.assertEqual(provider.source_model, "/tmp/ggml-small.en.bin")
        self.assertEqual(provider.model, "small.en")
        self.assertEqual(provider.threads, 8)

    def test_build_fast_whisper_provider_accepts_direct_model_id(self) -> None:
        provider = build_stt_provider(
            {
                "stt_provider": "fast-whisper",
                "stt_whisper_model": "small.en",
                "stt_whisper_threads": 8,
            }
        )

        self.assertIsInstance(provider, FastWhisperProvider)
        self.assertEqual(provider.source_model, "small.en")
        self.assertEqual(provider.model, "small.en")
        self.assertEqual(provider.threads, 8)

    def test_attempt_stt_warmup_starts_fast_whisper_only(self) -> None:
        FastWhisperProvider._WARMUP_STARTED = False
        with patch.object(FastWhisperProvider, "begin_warmup", return_value=True) as begin_warmup:
            attempt_stt_warmup(
                {
                    "stt_provider": "fast-whisper",
                    "stt_whisper_model": "small.en",
                    "stt_whisper_threads": 8,
                }
            )

        begin_warmup.assert_called_once()

    def test_attempt_stt_warmup_skips_non_fast_whisper(self) -> None:
        with patch.object(FastWhisperProvider, "begin_warmup", return_value=True) as begin_warmup:
            attempt_stt_warmup(
                {
                    "stt_provider": "whisper.cpp",
                    "stt_whisper_binary": "/tmp/whisper-cli",
                    "stt_whisper_model": "/tmp/ggml-small.en.bin",
                    "stt_whisper_threads": 8,
                }
            )

        begin_warmup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
