from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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
from oracle_app.provider_bridges.openclaw.adapters.http import generate_suggestions_http
from oracle_app.provider_bridges.openclaw.adapters.ssh_cli import (
    _isolated_session_id,
    _remote_script,
    generate_suggestions_ssh_cli,
)
from oracle_app.provider_bridges.openclaw.schemas import OpenClawBridgeOptions
from oracle_app.admin_suggestions_routes import (
    admin_generate_suggestions_http,
    admin_openclaw_status_http,
)
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.domain_models import (
    OpenAILunaSuggestionsProvider,
    OpenClawSshCliProvider,
)
from oracle_app.configuration.information_runtime_settings import SuggestionsRuntimeSettings
from oracle_app.inference_bridges import InferenceBridgeError, InferenceProviderResponse
from oracle_app.provider_bridges.openai_suggestions import generate_suggestions_openai_luna
from oracle_app.suggestions.canonical import CanonicalSuggestionsExecution
from oracle_app.suggestions.models import SuggestionGenerateRequest, SuggestionReviewRequest
from oracle_app.suggestions.collectors import collect_sources
from oracle_app.suggestions.packet import MAX_SUGGESTIONS_PACKET_BYTES, build_packet
from oracle_app.suggestions.redaction import redact_secrets
from oracle_app.suggestions.service import generate_suggestion_run, review_suggestion_item
from oracle_app.suggestions.storage import (
    create_run,
    ensure_storage,
    get_current_exchange,
    get_suggestion,
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
        created = insert_suggestions(
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
        ).created[0]

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
        repeat_result = insert_suggestions(
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
        self.assertEqual(repeat_result.created, [])
        self.assertEqual(repeat_result.suppressed[0]["similar_to_id"], created["id"])

        with_new_evidence = insert_suggestions(
            run_id_2,
            [
                {
                    "title": "Fix noisy wake logs",
                    "severity": "medium",
                    "category": "oracle",
                    "source": "oracle",
                    "summary": "Noise is still present.",
                    "evidence": ["line", "new line from a later collection window"],
                    "suggested_action": "Tune rejection.",
                    "confidence": 0.7,
                }
            ],
            mock=False,
        )
        self.assertEqual(len(with_new_evidence.created), 1)
        self.assertEqual(with_new_evidence.created[0]["similar_to_id"], created["id"])

    def test_packet_exposes_partial_collection_provenance_and_bounds(self) -> None:
        def available(**_kwargs):
            return {"ok": True, "value": "usable"}

        def failed(**_kwargs):
            raise RuntimeError("provider unavailable")

        with patch(
            "oracle_app.suggestions.collectors._selected_collectors",
            return_value=[("oracle", available), ("home_assistant", failed)],
        ), patch(
            "oracle_app.suggestions.collectors._collect_review_history",
            return_value={"count": 0, "omitted": 0, "items": []},
        ):
            packet, statuses = build_packet(
                run_id="run-partial",
                run_type="all_sources",
                window_start="2026-09-01T00:00:00Z",
                window_end="2026-09-02T00:00:00Z",
                reason=None,
                custom_prompt=None,
                max_suggestions=4,
            )

        self.assertEqual(packet["collection"]["status"], "partial")
        self.assertEqual(packet["collection"]["available_sources"], ["oracle"])
        self.assertEqual(packet["collection"]["unavailable_sources"], ["home_assistant"])
        self.assertEqual(statuses["home_assistant"]["status"], "unavailable")
        self.assertEqual(
            packet["source_sections"]["oracle"]["_provenance"]["authority"],
            "canonical_brain_composition_and_brain_journal",
        )
        self.assertEqual(packet["collection"]["bounds"]["review_history_items"], 50)

    def test_packet_has_a_real_serialized_bound_and_discloses_omissions(self) -> None:
        def huge(**_kwargs):
            return {"items": ["x" * 10000 for _ in range(200)]}

        with patch(
            "oracle_app.suggestions.collectors._selected_collectors",
            return_value=[("librenms", huge)],
        ), patch(
            "oracle_app.suggestions.collectors._collect_review_history",
            return_value={"count": 0, "omitted": 0, "items": []},
        ):
            packet, _statuses = build_packet(
                run_id="run-bounded",
                run_type="librenms",
                window_start="2026-09-01T00:00:00Z",
                window_end="2026-09-02T00:00:00Z",
                reason=None,
                custom_prompt=None,
                max_suggestions=4,
            )

        serialized = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        self.assertLessEqual(len(serialized), MAX_SUGGESTIONS_PACKET_BYTES)
        self.assertTrue(packet["collection"]["omissions"])
        self.assertEqual(packet["collection"]["bounds"]["packet_bytes"], 131072)

    def test_unconfigured_librenms_is_not_counted_as_available_evidence(self) -> None:
        sections, statuses = collect_sources("librenms", canonical_composition=None)

        self.assertFalse(statuses["librenms"]["ok"])
        self.assertEqual(statuses["librenms"]["status"], "unavailable")
        self.assertEqual(sections["librenms"]["_availability"], "unavailable")

    def test_all_failed_collection_does_not_call_openclaw(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "ssh_cli"}
        packet = {
            "run_id": "run",
            "collection": {"status": "unavailable"},
            "source_sections": {},
        }
        with patch(
            "oracle_app.suggestions.service.build_packet",
            return_value=(packet, {"oracle": {"status": "unavailable", "ok": False}}),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure_class"], "collection_unavailable")
        self.assertEqual(result["run"]["openclaw_status"], "not_called")
        execution.generate.assert_not_called()

    def test_configured_limit_caps_requested_suggestion_count(self) -> None:
        execution = CanonicalSuggestionsExecution(
            SuggestionsRuntimeSettings(
                enabled=True,
                provider_id="advisor",
                provider=OpenClawSshCliProvider(
                    adapter="ssh_cli",
                    target="oracle@advisor.invalid",
                    identity_file="/tmp/advisor-key",
                    cli_path="/opt/openclaw/bin/openclaw",
                    cli_mode="agent",
                    agent="oracle_advisor",
                ),
                max_suggestions=6,
            )
        )
        self.assertEqual(execution.max_suggestions(100), 6)
        self.assertEqual(execution.max_suggestions(3), 3)
        self.assertEqual(execution.status()["model_authority"], "oracle_provider_configuration")

    def test_direct_luna_execution_uses_shared_bounded_contract(self) -> None:
        execution = CanonicalSuggestionsExecution(
            SuggestionsRuntimeSettings(
                enabled=True,
                provider_id="direct_luna",
                provider=OpenAILunaSuggestionsProvider(
                    adapter="openai_luna",
                    credential_secret="OPENAI_LUNA_API_KEY",
                ),
                max_suggestions=4,
                resolved_api_key="test-secret",
            )
        )
        response = InferenceProviderResponse(
            text=json.dumps(
                {
                    "suggestions": [
                        {
                            "title": "Review bounded observation",
                            "severity": "low",
                            "category": "oracle",
                            "source": "oracle",
                            "summary": "The packet contains a bounded observation.",
                            "evidence": ["bounded observation"],
                            "suggested_action": "Review the observation.",
                            "recommended_oracle_action": None,
                            "confidence": 0.8,
                            "requires_review": False,
                        }
                    ]
                }
            ),
            model="gpt-5.6-luna",
            request_id="resp_test",
        )
        with patch(
            "oracle_app.provider_bridges.openai_suggestions.OpenAILunaInferenceBridge.generate",
            return_value=response,
        ) as generate:
            result = execution.generate(
                {"run_id": "direct-run", "observation": "bounded observation"},
                max_suggestions=4,
                use_mock=False,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "openai_luna")
        self.assertEqual(result["suggestions"][0]["title"], "Review bounded observation")
        self.assertTrue(result["suggestions"][0]["requires_review"])
        self.assertEqual(result["raw_response"]["request_id"], "resp_test")
        call = generate.call_args.kwargs
        self.assertIsNone(call["system"])
        self.assertEqual(call["json_schema"]["properties"]["suggestions"]["maxItems"], 4)
        self.assertIn("BEGIN_ORACLE_DIAGNOSTIC_PACKET", call["prompt"])
        status = execution.status()
        self.assertEqual(status["mode"], "direct")
        self.assertEqual(status["model_location"], "cloud")
        self.assertTrue(status["credential_configured"])

    def test_direct_luna_failure_is_bounded_and_does_not_fail_over(self) -> None:
        with patch(
            "oracle_app.provider_bridges.openai_suggestions.OpenAILunaInferenceBridge.generate",
            side_effect=InferenceBridgeError("timeout", "sensitive transport detail"),
        ):
            result = generate_suggestions_openai_luna(
                {"run_id": "direct-failure"},
                base_url="https://api.openai.com",
                model="gpt-5.6-luna",
                api_key="test-secret",
                timeout_seconds=180,
                max_output_tokens=4096,
                max_suggestions=4,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "transport")
        self.assertNotIn("sensitive transport detail", " ".join(result.errors))

    def test_internal_websocket_option_is_rejected_as_unsupported(self) -> None:
        with self.assertRaises(ValueError):
            OpenClawBridgeOptions(adapter="websocket")

    def test_bridge_rejects_evidence_free_agent_output(self) -> None:
        with patch("oracle_app.provider_bridges.openclaw.adapters.ssh_cli.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            mock_run.return_value.stdout = json.dumps(
                {
                    "payloads": [
                        {
                            "text": json.dumps(
                                {
                                    "suggestions": [
                                        {
                                            "title": "Guess without evidence",
                                            "summary": "This has no packet support.",
                                            "evidence": [],
                                            "suggested_action": "Do something.",
                                        }
                                    ]
                                }
                            )
                        }
                    ]
                }
            )
            result = generate_suggestions_ssh_cli(
                {"run_id": "run-no-evidence"},
                OpenClawBridgeOptions(adapter="ssh_cli", ssh_target="oracle@advisor.invalid"),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "response_validation")
        self.assertIn("concrete evidence", result.errors[0])

    def test_http_bridge_bounds_timeout_and_malformed_returned_packet(self) -> None:
        options = OpenClawBridgeOptions(
            adapter="http",
            base_url="http://advisor.invalid",
            endpoint_path="/suggestions",
            timeout_seconds=3,
        )
        with patch(
            "oracle_app.provider_bridges.openclaw.adapters.http.request.urlopen",
            side_effect=TimeoutError(),
        ):
            timed_out = generate_suggestions_http({"run_id": "timeout"}, options)
        self.assertFalse(timed_out.ok)
        self.assertEqual(timed_out.failure_class, "transport")

        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with patch(
            "oracle_app.provider_bridges.openclaw.adapters.http.request.urlopen",
            return_value=response,
        ):
            malformed = generate_suggestions_http({"run_id": "malformed"}, options)
        self.assertFalse(malformed.ok)
        self.assertEqual(malformed.failure_class, "response_validation")

        oversized = MagicMock()
        oversized.read.return_value = b"x" * 524289
        oversized.__enter__.return_value = oversized
        oversized.__exit__.return_value = False
        with patch(
            "oracle_app.provider_bridges.openclaw.adapters.http.request.urlopen",
            return_value=oversized,
        ):
            too_large = generate_suggestions_http({"run_id": "too-large"}, options)
        self.assertFalse(too_large.ok)
        self.assertEqual(too_large.failure_class, "response_validation")
        oversized.read.assert_called_once_with(524289)

    def test_http_bridge_keeps_valid_items_and_reports_invalid_items(self) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "suggestions": [
                    {
                        "title": "Review unavailable sensor",
                        "summary": "A bounded current snapshot reports it unavailable.",
                        "suggested_action": "Review the sensor.",
                        "evidence": ["sensor.example unavailable"],
                        "confidence": 0.7,
                    },
                    {"title": "Unsupported guess", "evidence": []},
                ]
            }
        ).encode()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with patch(
            "oracle_app.provider_bridges.openclaw.adapters.http.request.urlopen",
            return_value=response,
        ):
            result = generate_suggestions_http(
                {"run_id": "partial-return"},
                OpenClawBridgeOptions(adapter="http", base_url="http://advisor.invalid"),
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(len(result.errors), 1)

    def test_partial_response_validation_is_visible_in_completed_run(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "http"}
        execution.generate.return_value = {
            "ok": True,
            "provider": "openclaw",
            "adapter": "http",
            "raw_response": {},
            "suggestions": [],
            "errors": ["Suggestion item 2 is invalid."],
            "mock": False,
        }
        with patch(
            "oracle_app.suggestions.service.build_packet",
            return_value=(
                {"run_id": "run", "collection": {"status": "available"}},
                {"oracle": {"status": "available", "ok": True}},
            ),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["openclaw_status"], "partial")
        self.assertEqual(result["run"]["failure_class"], "response_validation")
        self.assertIn("item 2", result["run"]["error"])

    def test_redaction_removes_secret_fields(self) -> None:
        payload = {
            "token": "abc",
            "nested": {
                "api_key": "def",
                "safe": "Authorization: Bearer abc123 url=https://host/path?access_token=xyz",
            },
        }
        self.assertEqual(
            redact_secrets(payload),
            {
                "token": "[REDACTED]",
                "nested": {
                    "api_key": "[REDACTED]",
                    "safe": "Authorization: [REDACTED] url=https://host/path?access_token=[REDACTED]",
                },
            },
        )

    def test_memory_owned_schema_adds_slice_9_run_truth_to_existing_store(self) -> None:
        ensure_storage(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("ALTER TABLE suggestion_runs DROP COLUMN suppressed_count")
            conn.execute("ALTER TABLE suggestion_runs DROP COLUMN collection_status")
            conn.execute("ALTER TABLE suggestion_runs DROP COLUMN failure_class")
        ensure_storage(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(suggestion_runs)")
            }
        self.assertTrue({"suppressed_count", "collection_status", "failure_class"} <= columns)

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
            patch(
                "oracle_app.suggestions.collectors.query_events",
                return_value=[
                    {
                        "event_id": "evt-1",
                        "observed_at": "2026-09-01T12:00:00+00:00",
                        "event_type": "command_failed",
                        "category": "command",
                        "severity": "warning",
                        "payload": {"raw_transcript": "private payload is excluded"},
                    }
                ],
            ) as events,
        ):
            sections, statuses = collect_sources(
                "oracle",
                canonical_composition=composition,
                window_start="2026-09-01T00:00:00+00:00",
                window_end="2026-09-02T00:00:00+00:00",
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
        self.assertIs(network.call_args.kwargs["canonical_execution"], composition.network_execution)
        query = events.call_args.args[0]
        self.assertEqual(query.observed_after, "2026-09-01T00:00:00+00:00")
        self.assertEqual(query.observed_before, "2026-09-02T00:00:00+00:00")
        self.assertEqual(query.limit, 201)
        self.assertNotIn("payload", sections["oracle"]["memory_events"]["items"][0])

    def test_canonical_librenms_collector_uses_normalized_network_snapshot(self) -> None:
        network = SimpleNamespace(status_snapshot=lambda **_kwargs: {"status": "healthy"})
        composition = SimpleNamespace(network_execution=network)
        sections, statuses = collect_sources(
            "librenms",
            canonical_composition=composition,
        )

        self.assertTrue(statuses["librenms"]["ok"])
        self.assertEqual(sections["librenms"]["status"]["status"], "healthy")

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

        with patch(
            "oracle_app.suggestions.service.build_packet",
            return_value=({"run_id": "run"}, {"oracle": {"ok": True}}),
        ) as packet:
            status = admin_openclaw_status_http(request)
            result = admin_generate_suggestions_http(
                request,
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
            )

        self.assertTrue(status["configured"])
        self.assertTrue(result["ok"])
        self.assertIs(packet.call_args.kwargs["canonical_composition"], composition)
        execution.generate.assert_called_once()

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
                model="ollama/gpt-oss:20b",
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
        self.assertEqual(request["options"]["model"], "ollama/gpt-oss:20b")
        self.assertEqual(
            request["options"]["session_id"],
            _isolated_session_id({"run_id": "run-1"}),
        )
        self.assertRegex(
            request["options"]["session_id"],
            r"^oracle-suggestions-[0-9a-f]{32}$",
        )
        self.assertIn('cmd.extend(["--session-id", session_id])', _remote_script())
        self.assertIn('cmd.extend(["--model", model])', _remote_script())
        self.assertIn("BEGIN_ORACLE_DIAGNOSTIC_PACKET", request["prompt"])
        self.assertIn("END_ORACLE_DIAGNOSTIC_PACKET", request["prompt"])

    @patch("oracle_app.provider_bridges.openclaw.adapters.ssh_cli.subprocess.run")
    def test_ssh_cli_adapter_rejects_oversized_output(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = "x" * 524289

        result = generate_suggestions_ssh_cli(
            {"run_id": "run-oversized"},
            OpenClawBridgeOptions(adapter="ssh_cli", ssh_target="oracle@advisor.invalid"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "response_validation")
        self.assertEqual(result.raw_response, {})

    def test_generate_saves_packet_and_failed_openclaw_response(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "http"}
        execution.generate.return_value = {
            "ok": False,
            "provider": "openclaw",
            "adapter": "http",
            "raw_response": {},
            "suggestions": [],
            "errors": ["OpenClaw HTTP base URL is not configured."],
            "mock": False,
        }
        with (
            patch("oracle_app.suggestions.service.build_packet", return_value=({"run_id": "run", "token": "[REDACTED]"}, {"oracle": {"ok": True}})),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual(list_suggestions(), [])
        exchange = get_current_exchange()
        self.assertEqual(exchange["packet"]["token"], "[REDACTED]")
        self.assertEqual(exchange["response"]["adapter"], "http")

    def test_packet_construction_exception_leaves_a_terminal_run(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "ssh_cli"}
        with patch(
            "oracle_app.suggestions.service.build_packet",
            side_effect=RuntimeError("sensitive collector detail"),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual(result["run"]["openclaw_status"], "not_called")
        self.assertEqual(result["run"]["failure_class"], "integration_failure")
        self.assertNotIn("sensitive collector detail", result["run"]["error"])
        execution.generate.assert_not_called()

    def test_provider_exception_leaves_a_terminal_run(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "ssh_cli"}
        execution.generate.side_effect = RuntimeError("sensitive provider detail")
        with patch(
            "oracle_app.suggestions.service.build_packet",
            return_value=(
                {"run_id": "run", "collection": {"status": "available"}},
                {"oracle": {"status": "available", "ok": True}},
            ),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual(result["run"]["failure_class"], "integration_failure")
        self.assertNotIn("sensitive provider detail", result["run"]["error"])

    def test_generation_persists_deterministic_review_suppression_truth(self) -> None:
        reviewed_run = create_run(
            run_type="oracle",
            window_start="2026-09-01T00:00:00+00:00",
            window_end="2026-09-02T00:00:00+00:00",
            reason="review baseline",
            custom_prompt=None,
            mock=False,
        )
        reviewed = insert_suggestions(
            reviewed_run,
            [
                {
                    "title": "Review noisy wake logs",
                    "severity": "medium",
                    "category": "oracle",
                    "source": "oracle",
                    "summary": "Repeated noise exists.",
                    "evidence": ["wake rejected at 01:00"],
                    "suggested_action": "Review wake rejection thresholds.",
                    "confidence": 0.7,
                }
            ],
            mock=False,
        ).created[0]
        review_suggestion(
            reviewed["id"],
            {
                "status": "rejected",
                "notes": "Already investigated.",
                "correction_text": "",
                "rejection_reason": "No defect found.",
                "future_automation_candidate": False,
                "suppress_if_repeated": True,
            },
        )
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 5
        execution.status.return_value = {"adapter": "ssh_cli"}
        execution.generate.return_value = {
            "ok": True,
            "provider": "openclaw",
            "adapter": "ssh_cli",
            "raw_response": {},
            "suggestions": [reviewed["raw_openclaw_item"]],
            "errors": [],
            "failure_class": None,
            "mock": False,
        }
        packet = {
            "run_id": "new-run",
            "collection": {"status": "available"},
            "source_sections": {},
        }
        with patch(
            "oracle_app.suggestions.service.build_packet",
            return_value=(packet, {"oracle": {"status": "available", "ok": True}}),
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle", wait_for_completion=True),
                canonical_execution=execution,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["suppressed_count"], 1)
        self.assertEqual(result["run"]["suppressed_count"], 1)
        self.assertEqual(result["run"]["collection_status"], "available")
        self.assertEqual(len(list_suggestions()), 1)
        self.assertEqual(get_current_exchange()["response"]["oracle_intake"]["suppressed_count"], 1)

    def test_generate_queues_background_run_without_waiting_for_openclaw(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 10
        execution.status.return_value = {"adapter": "ssh_cli"}
        with (
            patch("oracle_app.suggestions.service.build_packet", return_value=({"run_id": "run"}, {"oracle": {"ok": True}})),
            patch("oracle_app.suggestions.service.threading.Thread") as mock_thread,
        ):
            result = generate_suggestion_run(
                SuggestionGenerateRequest(run_type="oracle"),
                canonical_execution=execution,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["run"]["status"], "running")
        mock_thread.return_value.start.assert_called_once()

    def test_review_service_rejects_unknown_suggestion(self) -> None:
        with self.assertRaises(Exception):
            review_suggestion_item("missing", SuggestionReviewRequest(status="ignored"))

    def test_review_requires_reason_or_correction_and_never_grants_execution(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionReviewRequest(status="rejected")
        with self.assertRaises(ValueError):
            SuggestionReviewRequest(status="corrected")
        with self.assertRaises(ValueError):
            SuggestionReviewRequest(status="accepted", suppress_if_repeated=True)
        accepted = SuggestionReviewRequest(
            status="accepted",
            future_automation_candidate=True,
        )
        self.assertTrue(accepted.future_automation_candidate)
        self.assertFalse(hasattr(accepted, "execute"))

    def test_accepted_advisory_item_remains_durable_for_later_human_work(self) -> None:
        run_id = create_run(
            run_type="oracle",
            window_start="2026-09-01T00:00:00+00:00",
            window_end="2026-09-02T00:00:00+00:00",
            reason="accepted retention",
            custom_prompt=None,
            mock=False,
        )
        item = insert_suggestions(
            run_id,
            [
                {
                    "title": "Review a durable advisory",
                    "summary": "The evidence supports later human review.",
                    "evidence": ["bounded observation"],
                    "suggested_action": "Review it later.",
                }
            ],
            mock=False,
        ).created[0]
        review_suggestion(
            item["id"],
            {
                "status": "accepted",
                "notes": "Keep for later work.",
                "future_automation_candidate": False,
                "suppress_if_repeated": False,
            },
        )

        ensure_storage(self.db_path)
        reopened = get_suggestion(item["id"])
        self.assertEqual(reopened["status"], "accepted")
        self.assertEqual(reopened["review_notes"], "Keep for later work.")
        self.assertNotIn(item["id"], {entry["id"] for entry in review_history()})

    def test_window_requires_timezone_and_forward_order(self) -> None:
        execution = Mock(enabled=True)
        execution.max_suggestions.return_value = 5
        with self.assertRaises(Exception) as missing_timezone:
            generate_suggestion_run(
                SuggestionGenerateRequest(
                    run_type="oracle",
                    window_start="2026-09-01T00:00:00",
                    window_end="2026-09-02T00:00:00Z",
                    wait_for_completion=True,
                ),
                canonical_execution=execution,
            )
        self.assertEqual(missing_timezone.exception.status_code, 422)

        with self.assertRaises(Exception) as reversed_window:
            generate_suggestion_run(
                SuggestionGenerateRequest(
                    run_type="oracle",
                    window_start="2026-09-03T00:00:00Z",
                    window_end="2026-09-02T00:00:00Z",
                    wait_for_completion=True,
                ),
                canonical_execution=execution,
            )
        self.assertEqual(reversed_window.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
