from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, Request
from tts import TtsResult

from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app import api
from oracle_app.configuration import EffectiveConfig, GenerationStore, inspect_candidate
from oracle_app.configuration.bootstrap import (
    BrainConfigurationStartup,
    ConfigurationBootstrapSettings,
)
from oracle_app.configuration.playback_target_resolution import ResolvedPlaybackTarget
from oracle_app.configuration.request_source_resolution import ResolvedRequestSource
from oracle_app.schemas import DispatchPlan, TtsRequest
from oracle_app.schemas import CommandRequest
from oracle_app.satellite_projection_routes import satellite_projection_pull
from oracle_app.routing import choose_route


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class CanonicalBrainApplicationCompositionTests(unittest.TestCase):
    def test_fastapi_has_no_import_time_v1_composition(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            api.brain_application_composition()

    def test_canonical_startup_builds_complete_dependencies_without_starting_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            store_root = root / "store"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            GenerationStore(store_root).initialize("example-home")
            effective = self._effective_config(bundle)
            startup = BrainConfigurationStartup(
                mode="canonical",
                service_settings=ConfigurationBootstrapSettings(
                    bundle_root=bundle,
                    store_root=store_root,
                    socket_path=root / "configuration.sock",
                    authoring_mode="external_read_only",
                ),
                effective_config=effective,
            )

            composition = CanonicalBrainApplicationComposition.from_startup(startup)

            self.assertIs(composition.runtime.effective_config, effective)
            self.assertEqual(
                composition.runtime.brain.activation_generation_id,
                effective.activation_generation_id,
            )
            self.assertIsNotNone(composition.dispatch_registry.get("fallback_router"))
            self.assertIs(
                composition.dispatch_registry.get("home_assistant").household_settings,  # type: ignore[union-attr]
                composition.runtime.household,
            )
            self.assertIs(
                composition.dispatch_registry.get("home_assistant").home_assistant_settings,  # type: ignore[union-attr]
                composition.runtime.home_assistant,
            )
            self.assertTrue(
                composition.dispatch_registry.get("home_assistant").canonical_authority  # type: ignore[union-attr]
            )
            self.assertIs(
                composition.dispatch_registry.get("system").household_settings,  # type: ignore[union-attr]
                composition.runtime.household,
            )
            self.assertIs(
                composition.dispatch_registry.get("audiobook").household_settings,  # type: ignore[union-attr]
                composition.runtime.household,
            )
            self.assertTrue(
                composition.dispatch_registry.get("audiobook").canonical_playback_target  # type: ignore[union-attr]
            )
            self.assertTrue(
                composition.dispatch_registry.get("music").canonical_playback_target  # type: ignore[union-attr]
            )
            self.assertEqual(composition.projection_resolver.store.root, store_root)
            self.assertIs(
                composition.playback_target_resolver.fleet,
                composition.runtime.satellites,
            )
            self.assertIs(
                composition.notification_execution.settings,
                composition.runtime.notifications,
            )
            self.assertIs(
                composition.notification_execution.home_assistant,
                composition.runtime.home_assistant,
            )
            self.assertIs(
                composition.notification_execution.satellites,
                composition.runtime.satellites,
            )
            with patch(
                "oracle_app.routing_helpers.load_home_assistant_cache",
                return_value={"rooms": [], "entities": []},
            ):
                canonical_route = choose_route(
                    "make the lounge brighter",
                    source="ephemeral_http",
                    session_id="canonical-home-route",
                    registry=composition.route_registry,
                    household_settings=composition.runtime.household,
                )
            self.assertEqual(canonical_route.target, "home_assistant")
            self.assertIn("living room", canonical_route.normalized_text)
            self.assertFalse((root / "configuration.sock").exists())

            previous = getattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
            try:
                with patch.dict(
                    "os.environ",
                    {"ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT": str(root / "wake-capture")},
                    clear=False,
                ):
                    api.install_brain_application_composition(api.app, composition)
                self.assertIs(
                    api.app.state.wake_capture_upload_service.resolver,
                    composition.projection_resolver,
                )
                with patch(
                    "oracle_app.configuration.playback_target_resolution.CanonicalPlaybackTargetResolver.resolve",
                    return_value=ResolvedPlaybackTarget(
                        source_id="living_room_voice",
                        satellite_id="living_room_satellite",
                        resolution="explicit",
                    ),
                ):
                    targeted_payload, target_resolution, target_error = (
                        api._apply_canonical_playback_target(
                            CommandRequest(
                                text="play music",
                                source="ephemeral_http",
                                session_id="canonical-target-plan",
                                playback_target_source_id="living_room_voice",
                            ),
                            route_target="music",
                            request_source=ResolvedRequestSource(
                                "ephemeral_http",
                                "ephemeral",
                                "none",
                            ),
                        )
                    )
                self.assertEqual(
                    targeted_payload.playback_target_source_id,
                    "living_room_voice",
                )
                self.assertEqual(target_resolution, "explicit")
                self.assertIsNone(target_error)
                self.assertIs(
                    api.app.state.satellite_projection_resolver,
                    composition.projection_resolver,
                )
                dispatch = DispatchPlan(
                    target="fallback_router",
                    hook="fallback_router.decide",
                    payload={"prompt": "hello", "source": "test", "session_id": "session-1"},
                    status="planned",
                )
                with (
                    patch("oracle_app.user_context.get_source_registry") as legacy_context_sources,
                    patch("oracle_app.user_context.get_user_registry") as legacy_context_users,
                    patch.object(
                        composition.core_consumers.tts_provider,
                        "synthesize",
                        return_value=TtsResult(b"audio", "audio/wav", "disabled-test"),
                    ),
                ):
                    response = api.synthesize_speech(TtsRequest(text="Hello"))
                    result = api._execute_application_dispatch(dispatch)
                    command_response = api.command_http_request(
                        CommandRequest(
                            text="what time is it",
                            source="claimed_stable_source",
                            session_id=None,
                        ),
                        Request(
                            {
                                "type": "http",
                                "method": "POST",
                                "path": "/api/conversation/command",
                                "query_string": b"",
                                "headers": [],
                                "app": api.app,
                            }
                        ),
                    )
                    with self.assertRaisesRegex(HTTPException, "authentication failed") as invalid_source:
                        api.command_http_request(
                            CommandRequest(
                                text="what time is it",
                                source="claimed_stable_source",
                                session_id="canonical-invalid-source",
                            ),
                            Request(
                                {
                                    "type": "http",
                                    "method": "POST",
                                    "path": "/api/conversation/command",
                                    "query_string": b"",
                                    "headers": [(b"authorization", b"Bearer wrong-token")],
                                    "app": api.app,
                                }
                            ),
                        )
                    with self.assertRaises(HTTPException) as malformed_credential:
                        api.command_http_request(
                            CommandRequest(
                                text="what time is it",
                                source="claimed_stable_source",
                                session_id="canonical-malformed-source",
                            ),
                            Request(
                                {
                                    "type": "http",
                                    "method": "POST",
                                    "path": "/api/conversation/command",
                                    "query_string": b"",
                                    "headers": [(b"authorization", b"Basic not-supported")],
                                    "app": api.app,
                                }
                            ),
                        )
                    health_response = api.health_config(
                        Request(
                            {
                                "type": "http",
                                "method": "GET",
                                "path": "/api/admin/health/config",
                                "query_string": b"",
                                "headers": [],
                                "app": api.app,
                            }
                        )
                    )
                    health_text_response = api.health_config(
                        Request(
                            {
                                "type": "http",
                                "method": "GET",
                                "path": "/api/admin/health/config",
                                "query_string": b"format=text",
                                "headers": [],
                                "app": api.app,
                            }
                        )
                    )

                    envelope = MagicMock()
                    envelope.canonical_bytes.return_value = b'{"projection":"ok"}'
                    with patch.object(
                        composition.projection_resolver,
                        "resolve_pull",
                        return_value=envelope,
                    ):
                        projection_response = satellite_projection_pull(
                            "example_satellite",
                            Request(
                                {
                                    "type": "http",
                                    "method": "GET",
                                    "path": "/api/satellite/projection/example_satellite",
                                    "query_string": b"",
                                    "headers": [(b"authorization", b"Bearer test-credential")],
                                    "app": api.app,
                                }
                            ),
                        )

                self.assertIs(api.brain_application_composition(), composition)
                self.assertEqual(response.body, b"audio")
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.result["error"], "fallback_router_disabled")
                self.assertEqual(
                    command_response.dispatch.payload["source"],
                    "ephemeral_http",
                )
                self.assertTrue(
                    str(command_response.effective_session_id).startswith("ephemeral-")
                )
                self.assertEqual(invalid_source.exception.status_code, 401)
                self.assertEqual(malformed_credential.exception.status_code, 401)
                self.assertEqual(
                    command_response.dispatch.payload["user_resolution_source"],
                    "household_default",
                )
                health_payload = json.loads(health_response.body)
                self.assertEqual(health_payload["configuration"]["mode"], "canonical")
                self.assertEqual(
                    health_payload["configuration"]["applied_generation"]["config_revision"],
                    effective.config_revision,
                )
                self.assertIn("Applied configuration:", health_text_response.body.decode("utf-8"))
                self.assertIn(effective.config_revision, health_text_response.body.decode("utf-8"))
                self.assertEqual(projection_response.body, b'{"projection":"ok"}')
                legacy_context_sources.assert_not_called()
                legacy_context_users.assert_not_called()
            finally:
                if previous is not None:
                    api.install_brain_application_composition(api.app, previous)
                elif hasattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY):
                    delattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY)

    def test_noncanonical_or_incomplete_startup_is_rejected(self) -> None:
        for startup in (
            BrainConfigurationStartup("legacy_migration", None, None),
            BrainConfigurationStartup("canonical", None, None),
        ):
            with self.subTest(mode=startup.mode, complete=startup.effective_config is not None):
                with self.assertRaises(ValueError):
                    CanonicalBrainApplicationComposition.from_startup(startup)

    def test_lifespan_resolves_once_and_never_reads_v1_configuration_in_canonical_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            store_root = root / "store"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            GenerationStore(store_root).initialize("example-home")
            startup = BrainConfigurationStartup(
                mode="canonical",
                service_settings=ConfigurationBootstrapSettings(
                    bundle_root=bundle,
                    store_root=store_root,
                    socket_path=root / "configuration.sock",
                    authoring_mode="external_read_only",
                ),
                effective_config=self._effective_config(bundle),
            )
            previous = getattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)

            async def run_lifespan() -> None:
                async with api.lifespan(api.app):
                    composition = api.brain_application_composition()
                    self.assertIsInstance(composition, CanonicalBrainApplicationComposition)

            try:
                with ExitStack() as stack:
                    resolve_startup = stack.enter_context(patch(
                        "oracle_app.api.resolve_brain_configuration_startup",
                        return_value=startup,
                    ))
                    stack.enter_context(patch("oracle_app.api.safe_record_event", return_value=True))
                    seed_sources = stack.enter_context(patch("oracle_app.api.safe_seed_memory_sources", return_value=True))
                    stack.enter_context(patch("oracle_app.api.safe_reconcile_interrupted_orchestration_runs", return_value=0))
                    stack.enter_context(patch("oracle_app.api.safe_reconcile_interrupted_network_controls", return_value=0))
                    stack.enter_context(patch("oracle_app.api.safe_restore_network_control_results_from_memory", return_value=0))
                    complete_host_restart = stack.enter_context(patch("oracle_app.api.safe_complete_pending_local_host_restart", return_value={"status": "none"}))
                    stack.enter_context(patch("oracle_app.api.safe_complete_pending_local_service_restart", return_value={"status": "none"}))
                    stt_warmup = stack.enter_context(patch("oracle_app.api.attempt_stt_provider_warmup"))
                    inference_warmup = stack.enter_context(patch("oracle_app.api.attempt_fallback_router_warmup"))
                    host_local = stack.enter_context(patch("oracle_app.api.start_brain_configuration_host_local_runtime"))
                    routine_worker = stack.enter_context(patch("oracle_app.api.routine_scheduler_loop", new_callable=AsyncMock))
                    home_worker = stack.enter_context(patch("oracle_app.api.home_automation_scheduler_loop", new_callable=AsyncMock))
                    delivery_worker = stack.enter_context(patch("oracle_app.api.external_delivery_worker_loop", new_callable=AsyncMock))
                    asyncio.run(run_lifespan())

                composition = api.brain_application_composition()
                self.assertIsInstance(composition, CanonicalBrainApplicationComposition)
                resolve_startup.assert_called_once_with()
                seed_sources.assert_called_once_with(
                    composition.runtime.household,
                    composition.runtime.satellites,
                )
                complete_host_restart.assert_called_once_with(
                    canonical_execution=composition.network_execution,
                    canonical_authority=True,
                )
                stt_warmup.assert_called_once_with(composition.core_consumers.stt_provider)
                inference_warmup.assert_called_once_with(composition.core_consumers.inference)
                host_local.assert_called_once_with(startup=startup)
                routine_worker.assert_not_called()
                home_worker.assert_called_once_with(
                    home_assistant_settings=composition.runtime.home_assistant,
                    notification_submitter=composition.notification_execution.submit,
                )
                delivery_worker.assert_called_once_with(
                    canonical_execution=composition.notification_execution,
                )
            finally:
                if previous is not None:
                    api.install_brain_application_composition(api.app, previous)
                elif hasattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY):
                    delattr(api.app.state, api.BRAIN_APPLICATION_COMPOSITION_STATE_KEY)

    def _effective_config(self, bundle: Path) -> EffectiveConfig:
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
