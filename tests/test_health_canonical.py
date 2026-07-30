from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, Request

from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.health import (
    check_audiobook_health,
    check_calendar_health,
    check_home_assistant_health,
    check_librenms_health,
    check_music_health,
    check_news_health,
    check_ollama_health,
    check_stt_health,
    check_tts_health,
)
from oracle_app.health_routes import health_ollama_http, health_stt_http, health_tts_http


class CanonicalCoreHealthTests(unittest.TestCase):
    def test_disabled_canonical_providers_are_intentionally_unavailable(self) -> None:
        disabled_inference = SimpleNamespace(
            enabled=False,
            base_url=None,
            model=None,
            timeout_seconds=5,
        )
        responses = (
            check_audiobook_health(None, canonical_authority=True),
            check_calendar_health(canonical_execution=None, canonical_authority=True),
            check_home_assistant_health(None, canonical_authority=True),
            check_librenms_health(canonical_execution=None, canonical_authority=True),
            check_music_health(music_execution=None, canonical_authority=True),
            check_news_health(canonical_execution=None, canonical_authority=True),
            check_ollama_health(inference=disabled_inference, canonical_authority=True),
            check_stt_health(provider=None, canonical_authority=True),
            check_tts_health(provider=None, canonical_authority=True),
        )

        for response in responses:
            with self.subTest(response=type(response).__name__):
                self.assertEqual(response.status, "disabled")

    def test_core_health_routes_use_installed_canonical_consumers(self) -> None:
        stt_provider = Mock()
        stt_provider.status.return_value = SimpleNamespace(
            provider="fast-whisper",
            configured=True,
            available=True,
            detail="Ready.",
        )
        tts_provider = Mock()
        tts_provider.status.return_value = SimpleNamespace(
            provider="piper",
            configured=True,
            available=True,
            detail="Ready.",
        )
        core = SimpleNamespace(
            stt_provider=stt_provider,
            tts_provider=tts_provider,
            inference=SimpleNamespace(
                enabled=True,
                base_url="http://ollama.invalid",
                model="example-model",
                timeout_seconds=7,
            ),
        )
        application = FastAPI()
        application.state.brain_application_composition = CanonicalBrainApplicationComposition(
            runtime=Mock(),
            core_consumers=core,
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
        )
        request = Request({"type": "http", "app": application})
        response = Mock(status=200)
        response.read.return_value = b'{"version":"test"}'
        opened = Mock()
        opened.__enter__ = Mock(return_value=response)
        opened.__exit__ = Mock(return_value=False)

        with (
            patch("oracle_app.health.get_ollama_settings") as legacy_ollama,
            patch("oracle_app.health.get_stt_provider") as legacy_stt,
            patch("oracle_app.health.get_tts_provider") as legacy_tts,
            patch("oracle_app.health.request.urlopen", return_value=opened) as urlopen,
            patch("oracle_app.health_routes.safe_observe_provider_health"),
        ):
            ollama = health_ollama_http(request)
            stt = health_stt_http(request)
            tts = health_tts_http(request)

        self.assertEqual(ollama.status, "ok")
        self.assertEqual(stt.provider, "fast-whisper")
        self.assertEqual(tts.provider, "piper")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        legacy_ollama.assert_not_called()
        legacy_stt.assert_not_called()
        legacy_tts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
