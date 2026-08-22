from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_stub.__all__ = []
python_multipart_stub.__author__ = ""
python_multipart_stub.__copyright__ = ""
python_multipart_stub.__license__ = ""
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from oracle_app.provider_bridges.openclaw.client import generate_suggestions as bridge_generate
from oracle_app.provider_bridges.openclaw.adapters.ssh_cli import generate_suggestions_ssh_cli
from oracle_app.provider_bridges.openclaw.schemas import OpenClawBridgeOptions
from oracle_app.admin_suggestions_routes import (
    admin_generate_suggestions_http,
    admin_openclaw_status_http,
)
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.domain_models import OpenClawSshCliProvider
from oracle_app.configuration.information_runtime_settings import SuggestionsRuntimeSettings
from oracle_app.suggestions.canonical import CanonicalSuggestionsExecution
from oracle_app.suggestions.models import SuggestionGenerateRequest, SuggestionReviewRequest
from oracle_app.suggestions.collectors import collect_sources
from oracle_app.suggestions.redaction import redact_secrets
from oracle_app.suggestions.service import generate_suggestion_run, review_suggestion_item
from oracle_app.suggestions.storage import (
    create_run,
    get_current_exchange,
    insert_suggestions,
    list_suggestions,
    review_history,
    review_suggestion,
)


class SuggestionDomainTests(unittest.TestCase):
    def test_openclaw_bridge_default_cli_is_portable_name(self) -> None:
        self.assertEqual(OpenClawBridgeOptions().cli_path, "openclaw")

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.known_hosts_path = Path(self.tmpdir.name) / "known_hosts"
        self.known_hosts_path.write_text("advisor.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n")
        self.known_hosts_path.chmod(0o600)
        ssh_environment = patch.dict(
            "os.environ", {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(self.known_hosts_path)}
        )
        ssh_environment.start()
        self.addCleanup(ssh_environment.stop)
        self.db_path = Path(self.tmpdir.name) / "suggestions.sqlite3"
        patcher = patch("oracle_app.suggestions.storage.DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_storage_records_review_history_and_similarity(self) -> None:
        run_id = create_run(
            run_type="oracle",
            window_start="2026-04-01T00:00:00-04:00",
            window_end="2026-04-24T00:00:00-04:00",
            reason="test",
            custom_prompt=None,
            mock=False,
        )
        [created] = insert_suggestions(
            run_id,
            [
                {
                    "title": "Fix noisy wake logs",
                    "severity": "medium",
                    "category": "oracle",
                    "source": "oracle",
                    "summary": "Noise is present.",
                    "evidence": ["line"],
                    "suggested_action": "Tune rejection.",
                    "recommended_oracle_action": None,
                    "confidence": 0.7,
                    "requires_review": True,
                }
            ],
            mock=False,
        )

        updated = review_suggestion(
            created["id"],
            {
                "status": "rejected",
                "notes": "Not useful.",
                "correction_text": "",
                "rejection_reason": "Old evidence.",
                "future_automation_candidate": False,
                "suppress_if_repeated": True,
            },
        )

        self.assertEqual(updated["status"], "rejected")
        self.assertTrue(updated["suppress_if_repeated"])
        self.assertEqual(review_history()[0]["rejection_reason"], "Old evidence.")

        run_id_2 = create_run(
            run_type="oracle",
            window_start="2026-04-24T00:00:00-04:00",
            window_end="2026-04-25T00:00:00-04:00",
            reason="test",
            custom_prompt=None,
            mock=False,
        )
        [repeat] = insert_suggestions(
            run_id_2,
            [
                {
                    "title": "Fix noisy wake logs",
                    "severity": "medium",
                    "category": "oracle",
                    "source": "oracle",
                    "summary": "Noise is present.",
                    "evidence": ["line"],
                    "suggested_action": "Tune rejection.",
                    "confidence": 0.7,
                }
            ],
            mock=False,
        )
        self.assertEqual(repeat["similar_to_id"], created["id"])

    def test_redaction_removes_secret_fields(self) -> None:
        payload = {"token": "abc", "nested": {"api_key": "def", "safe": "value"}}
        self.assertEqual(
            redact_secrets(payload),
            {"token": "[REDACTED]", "nested": {"api_key": "[REDACTED]", "safe": "value"}},
        )

    def test_bridge_mock_is_explicitly_labeled(self) -> None:
        result = bridge_generate({"run_id": "run-1"}, {"adapter": "mock", "use_mock": True, "max_suggestions": 10})

        self.assertTrue(result["ok"])
        self.assertTrue(result["mock"])
        self.assertEqual(result["adapter"], "mock")
        self.assertIn("[MOCK]", result["suggestions"][0]["title"])

    def test_canonical_oracle_collector_uses_only_composition_dependencies(self) -> None:
        music = SimpleNamespace(settings=SimpleNamespace(playback_targets={"living_room_voice": object()}))
        composition = SimpleNamespace(
            runtime=SimpleNamespace(home_assistant=object()),
            calendar_execution=object(),
            music_execution=music,
            audiobook_execution=object(),
            news_execution=object(),
            network_execution=object(),
            core_consumers=SimpleNamespace(inference=object()),
            tts_provider=lambda: object(),
            stt_provider=lambda: object(),
        )
        health = {"status": "ok"}
        with (
            patch("oracle_app.suggestions.collectors.check_home_assistant_health", return_value=health) as home,
            patch("oracle_app.suggestions.collectors.check_calendar_health", return_value=health) as calendar,
            patch("oracle_app.suggestions.collectors.check_music_health", return_value=health) as music_health,
            patch("oracle_app.suggestions.collectors.check_audiobook_health", return_value=health) as audiobook,
            patch("oracle_app.suggestions.collectors.check_ollama_health", return_value=health) as ollama,
            patch("oracle_app.suggestions.collectors.check_news_health", return_value=health) as news,
            patch("oracle_app.suggestions.collectors.check_tts_health", return_value=health) as tts,
            patch("oracle_app.suggestions.collectors.check_stt_health", return_value=health) as stt,
            patch("oracle_app.suggestions.collectors.build_ui_network_health_snapshot", return_value=health) as network,
            patch("oracle_app.suggestions.collectors._read_brain_logs", return_value={"ok": True}),
        ):
            sections, statuses = collect_sources(
                "oracle",
                canonical_composition=composition,
                canonical_authority=True,
            )

        self.assertEqual(sections["oracle"]["configured_sources"], ["living_room_voice"])
        self.assertTrue(statuses["oracle"]["ok"])
        self.assertIs(home.call_args.args[0], composition.runtime.home_assistant)
        self.assertIs(calendar.call_args.kwargs["canonical_execution"], composition.calendar_execution)
        self.assertIs(music_health.call_args.kwargs["music_execution"], composition.music_execution)
        self.assertIs(audiobook.call_args.args[0], composition.audiobook_execution)
        self.assertIs(ollama.call_args.kwargs["inference"], composition.core_consumers.inference)
        self.assertIs(news.call_args.kwargs["canonical_execution"], composition.news_execution)
        self.assertIsNotNone(tts.call_args.kwargs["provider"])
        self.assertIsNotNone(stt.call_args.kwargs["provider"])
        self.assertTrue(network.call_args.kwargs["canonical_authority"])

    def test_canonical_librenms_collector_uses_normalized_network_snapshot(self) -> None:
        network = SimpleNamespace(status_snapshot=lambda **_kwargs: {"status": "healthy"})
        composition = SimpleNamespace(network_execution=network)
        with patch("oracle_app.config.get_librenms_settings") as legacy:
            sections, statuses = collect_sources(
                "librenms",
                canonical_composition=composition,
                canonical_authority=True,
            )

        self.assertTrue(statuses["librenms"]["ok"])
        self.assertEqual(sections["librenms"]["status"]["status"], "healthy")
        legacy.assert_not_called()

    def test_canonical_ssh_execution_uses_typed_long_running_timeout(self) -> None:
        execution = CanonicalSuggestionsExecution(
            SuggestionsRuntimeSettings(
                enabled=True,
                provider_id="advisor",
                provider=OpenClawSshCliProvider(
                    adapter="ssh_cli",
                    target="oracle@advisor.invalid",
                    identity_file="/tmp/advisor-key",
                    connect_timeout_seconds=8,
                    timeout_seconds=14400,
                    cli_path="/opt/openclaw/bin/openclaw",
                    cli_mode="agent",
                    agent="oracle_advisor",
                ),
                max_suggestions=10,
                resolved_password="secret-password",
            )
        )
        with patch("oracle_app.suggestions.canonical.generate_suggestions", return_value={"ok": True}) as generate:
            result = execution.generate(
                {"run_id": "run-1"},
                max_suggestions=7,
                use_mock=False,
            )

        self.assertTrue(result["ok"])
        options = generate.call_args.args[1]
        self.assertIsInstance(options, OpenClawBridgeOptions)
        self.assertEqual(options.timeout_seconds, 14400)
        self.assertEqual(options.ssh_connect_timeout_seconds, 8)
        self.assertEqual(options.max_suggestions, 7)

    def test_canonical_admin_status_and_generation_do_not_read_legacy_settings(self) -> None:
        execution = Mock(enabled=True)
        execution.status.return_value = {
            "ok": True,
            "provider": "openclaw",
            "adapter": "ssh_cli",
            "configured": True,
        }
        execution.max_suggestions.return_value = 10
        execution.generate.return_value = {
            "ok": True,
            "provider": "openclaw",
            "adapter": "ssh_cli",
            "raw_response": {},
            "suggestions": [],
            "errors": [],
            "mock": False,
        }
        application = FastAPI()
        composition = CanonicalBrainApplicationComposition(
            runtime=Mock(),
            core_consumers=Mock(),
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
            suggestions_execution=execution,
        )
        application.state.brain_application_composition = composition
        request = Request({"type": "http", "app": application})

        with (
            patch("oracle_app.suggestions.service.build_packet", return_value=({"run_id": "run"}, {"oracle": {"ok": True}})) as packet,
            patch("oracle_app.suggestions.service.get_openclaw_settings") as legacy,
        ):
            status = admin_openclaw_status_http(request)
            result = admin_generate_suggestions_http(
                request,
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
            )

        self.assertTrue(status["configured"])
        self.assertTrue(result["ok"])
        self.assertIs(packet.call_args.kwargs["canonical_composition"], composition)
        self.assertTrue(packet.call_args.kwargs["canonical_authority"])
        execution.generate.assert_called_once()
        legacy.assert_not_called()

    @patch("oracle_app.provider_bridges.openclaw.adapters.ssh_cli.subprocess.run")
    def test_ssh_cli_adapter_parses_openclaw_agent_output(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = json.dumps(
            {
                "ok": True,
                "payloads": [
                    {
                        "text": json.dumps(
                            {
                                "suggestions": [
                                    {
                                        "title": "Review stale HA entities",
                                        "severity": "low",
                                        "category": "home_assistant",
                                        "source": "home_assistant",
                                        "summary": "One entity is stale.",
                                        "evidence": ["sensor.example unavailable"],
                                        "suggested_action": "Review the entity.",
                                        "recommended_oracle_action": None,
                                        "confidence": 0.6,
                                        "requires_review": True,
                                    }
                                ]
                            }
                        )
                    }
                ],
            }
        )

        result = generate_suggestions_ssh_cli(
            {"run_id": "run-1", "token": "secret"},
            OpenClawBridgeOptions(
                adapter="ssh_cli",
                ssh_target="oracle@advisor.invalid",
                ssh_password="pw",
                ssh_identity_file="/tmp/key",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.adapter, "ssh_cli")
        self.assertEqual(result.suggestions[0]["title"], "Review stale HA entities")
        command = mock_run.call_args.args[0]
        self.assertEqual(command[:3], ["sshpass", "-e", "ssh"])
        self.assertNotIn("pw", command)
        self.assertEqual(mock_run.call_args.kwargs["env"]["SSHPASS"], "pw")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(f"UserKnownHostsFile={self.known_hosts_path}", command)
        self.assertIn("/tmp/key", command)
        request = json.loads(mock_run.call_args.kwargs["input"])
        self.assertEqual(request["options"]["cli_mode"], "agent")
        self.assertEqual(request["options"]["agent_name"], "oracle-advisor")
        self.assertIn("BEGIN_ORACLE_DIAGNOSTIC_PACKET", request["prompt"])
        self.assertIn("END_ORACLE_DIAGNOSTIC_PACKET", request["prompt"])

    def test_generate_saves_packet_and_failed_openclaw_response(self) -> None:
        with (
            patch("oracle_app.suggestions.service.build_packet", return_value=({"run_id": "run", "token": "[REDACTED]"}, {"oracle": {"ok": True}})),
            patch(
                "oracle_app.suggestions.service.get_openclaw_settings",
                return_value={"adapter": "http", "base_url": "", "endpoint_path": "", "timeout_seconds": 1, "max_suggestions": 10},
            ),
            patch(
                "oracle_app.suggestions.service.openclaw_generate_suggestions",
                return_value={
                    "ok": False,
                    "provider": "openclaw",
                    "adapter": "http",
                    "raw_response": {},
                    "suggestions": [],
                    "errors": ["OpenClaw HTTP base URL is not configured."],
                    "mock": False,
                },
            ),
        ):
            result = generate_suggestion_run(SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True))

        self.assertFalse(result["ok"])
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual(list_suggestions(), [])
        exchange = get_current_exchange()
        self.assertEqual(exchange["packet"]["token"], "[REDACTED]")
        self.assertEqual(exchange["response"]["adapter"], "http")

    def test_generate_queues_background_run_without_waiting_for_openclaw(self) -> None:
        with (
            patch("oracle_app.suggestions.service.build_packet", return_value=({"run_id": "run"}, {"oracle": {"ok": True}})),
            patch(
                "oracle_app.suggestions.service.get_openclaw_settings",
                return_value={
                    "adapter": "ssh_cli",
                    "ssh_target": "oracle@advisor.invalid",
                    "timeout_seconds": 14400,
                    "max_suggestions": 10,
                },
            ),
            patch("oracle_app.suggestions.service.threading.Thread") as mock_thread,
        ):
            result = generate_suggestion_run(SuggestionGenerateRequest(run_type="oracle"))

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["run"]["status"], "running")
        mock_thread.return_value.start.assert_called_once()

    def test_review_service_rejects_unknown_suggestion(self) -> None:
        with self.assertRaises(Exception):
            review_suggestion_item("missing", SuggestionReviewRequest(status="ignored"))


if __name__ == "__main__":
    unittest.main()
