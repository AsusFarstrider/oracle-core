from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from oracle_app.audiobook_runtime.canonical import CanonicalAudiobookExecution
from oracle_app.configuration import (
    AudiobookRuntimeSettings,
    EffectiveConfig,
    HomeAssistantRuntimeSettings,
    RoutineRuntimeSettings,
    inspect_candidate,
)
from oracle_app.orchestration_routine_canonical import CanonicalRoutineExecution
from oracle_app.orchestration_routines import resume_due_routines


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
PROJECTION_ACTIVATION_ID = "sat_activation_11111111111111111111111111111111"


class RoutineRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_role_selects_no_definition_or_trigger(self) -> None:
        settings = RoutineRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertEqual(dict(settings.definitions), {})
        self.assertEqual(dict(settings.global_voice_phrases), {})
        self.assertEqual(dict(settings.source_voice_phrases), {})

    def test_enabled_routine_binds_owner_sources_steps_and_trigger_indexes(self) -> None:
        settings = RoutineRuntimeSettings.from_effective_config(self._effective_config(enabled=True))

        routine = settings.definition("bedtime")
        self.assertIsNotNone(routine)
        self.assertEqual(routine.owner.id, "resident_one")  # type: ignore[union-attr]
        self.assertEqual(tuple(routine.sources), ("living_room_voice",))  # type: ignore[union-attr]
        steps = {item.definition.id: item for item in routine.steps}  # type: ignore[union-attr]
        self.assertEqual(steps["lights_off"].action_mapping.entity_id, "light.living_room")  # type: ignore[union-attr]
        self.assertEqual(steps["start_book"].audiobook_user_account.account_id, "primary")  # type: ignore[union-attr]
        self.assertEqual(steps["start_book"].playback_target.satellite_id, "living_room_satellite")  # type: ignore[union-attr]
        self.assertEqual(steps["check_lights"].state_mapping.entity_id, "light.living_room")  # type: ignore[union-attr]
        self.assertEqual(steps["check_lights"].remediation_action_mapping.entity_id, "light.living_room")  # type: ignore[union-attr]
        self.assertEqual(steps["check_playback"].native_remediation_action_id, "stop_audiobook")
        self.assertIs(settings.resolve_voice_trigger("start bedtime", source_id="other"), routine)
        self.assertIs(settings.resolve_voice_trigger("bedtime please", source_id="living_room_voice"), routine)
        self.assertIsNone(settings.resolve_voice_trigger("bedtime please", source_id="other"))
        self.assertNotIn("resident-audiobook-token", repr(settings))
        self.assertNotIn("control-secret-value", repr(settings))
        with self.assertRaises(TypeError):
            settings.definitions["other"] = routine  # type: ignore[index]

    def test_enabled_routine_cannot_bind_mapping_from_disabled_home_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_bundle(bundle)
            path = bundle / "domains" / "home-assistant.yaml"
            role = json.loads(path.read_text(encoding="utf-8"))
            role["enabled"] = False
            role["provider"] = None
            path.write_text(json.dumps(role), encoding="utf-8")

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        self.assertIn(
            "config.reference.disabled_capability",
            {finding.code for finding in inspection.report.validation_findings},
        )

    def test_enabled_routine_playback_source_must_be_admitted_by_audiobook_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_bundle(bundle)
            path = bundle / "domains" / "audiobooks.yaml"
            role = json.loads(path.read_text(encoding="utf-8"))
            role["playback"]["source_ids"] = []
            path.write_text(json.dumps(role), encoding="utf-8")

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        self.assertIn(
            "config.reference.disabled_capability",
            {finding.code for finding in inspection.report.validation_findings},
        )

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            RoutineRuntimeSettings.from_effective_config(effective)

    def test_canonical_start_and_continuation_use_frozen_definition_and_explicit_adapters(self) -> None:
        effective = self._effective_config(enabled=True)
        routine_settings = RoutineRuntimeSettings.from_effective_config(effective)
        audiobook_settings = AudiobookRuntimeSettings.from_effective_config(effective)
        execution = CanonicalRoutineExecution(
            settings=routine_settings,
            home_assistant=HomeAssistantRuntimeSettings.from_effective_config(effective),
            audiobooks=CanonicalAudiobookExecution(
                audiobook_settings,
                satellite_control_timeout_seconds=6,
            ),
        )
        calls: list[tuple[str, dict[str, object]]] = []

        def adapter(kind: str):
            def execute(**kwargs):
                calls.append((kind, kwargs))
                return {"ok": True, "status": "executed", "detail": f"{kind} complete"}

            return execute

        execution.adapters = MappingProxyType(
            {
                "ui_action": adapter("ui_action"),
                "audiobook_start": adapter("audiobook_start"),
                "audiobook_resume": adapter("audiobook_start"),
                "sleep_timer": adapter("sleep_timer"),
                "state_check": adapter("state_check"),
                "playback_check": adapter("playback_check"),
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "memory.sqlite3"
            with patch(
                "oracle_app.orchestration_routines.get_orchestration_settings",
                side_effect=AssertionError("canonical routine used V1 configuration"),
            ):
                run = execution.start(
                    "bedtime",
                    client_id="canonical-test",
                    inputs={"sleep_minutes": 1},
                    db_path=db_path,
                )
                self.assertEqual(run["status"], "waiting")
                self.assertEqual(
                    run["payload"]["config_revision"],
                    routine_settings.config_revision,
                )
                resumed = execution.resume_due(
                    now=datetime.now(timezone.utc) + timedelta(minutes=2),
                    db_path=db_path,
                )

        self.assertEqual(resumed[0]["status"], "completed")
        self.assertEqual(
            [kind for kind, _kwargs in calls],
            ["ui_action", "audiobook_start", "sleep_timer", "state_check", "playback_check"],
        )

    def test_canonical_home_assistant_adapters_use_bound_mappings(self) -> None:
        effective = self._effective_config(enabled=True)
        execution = CanonicalRoutineExecution(
            settings=RoutineRuntimeSettings.from_effective_config(effective),
            home_assistant=HomeAssistantRuntimeSettings.from_effective_config(effective),
            audiobooks=CanonicalAudiobookExecution(
                AudiobookRuntimeSettings.from_effective_config(effective),
                satellite_control_timeout_seconds=6,
            ),
        )

        with (
            patch(
                "oracle_app.orchestration_routine_canonical.HomeAssistantBridge.call_service"
            ) as call_service,
            patch(
                "oracle_app.orchestration_routine_canonical.HomeAssistantBridge.wait_for_entity_state",
                return_value={"state": "off"},
            ) as wait_for_state,
            patch(
                "oracle_app.orchestration_routine_canonical.HomeAssistantBridge.fetch_entity_state",
                return_value={"state": "off"},
            ) as fetch_state,
        ):
            action = execution.ui_action(
                action_id="lights_off",
                client_id="canonical-test",
            )
            check = execution.state_check(
                check_id="lights_state",
                expected_state="off",
                client_id="canonical-test",
            )

        self.assertTrue(action["ok"])
        self.assertTrue(check["ok"])
        call_service.assert_called_once_with(
            service_domain="light",
            service_name="turn_off",
            entity_id="light.living_room",
        )
        wait_for_state.assert_called_once_with("light.living_room", "off")
        fetch_state.assert_called_once_with("light.living_room")

    def test_canonical_audiobook_adapters_use_typed_execution_without_retired_authority_flag(self) -> None:
        effective = self._effective_config(enabled=True)
        audiobook_execution = CanonicalAudiobookExecution(
            AudiobookRuntimeSettings.from_effective_config(effective),
            satellite_control_timeout_seconds=6,
        )
        execution = CanonicalRoutineExecution(
            settings=RoutineRuntimeSettings.from_effective_config(effective),
            home_assistant=HomeAssistantRuntimeSettings.from_effective_config(effective),
            audiobooks=audiobook_execution,
        )

        with (
            patch(
                "oracle_app.orchestration_routine_canonical.start_current_audiobook_for_user",
                return_value={"ok": True},
            ) as start_audiobook,
            patch(
                "oracle_app.orchestration_routine_canonical.set_audiobook_sleep_timer_seconds",
                return_value={"ok": True},
            ) as set_timer,
        ):
            execution.audiobook_start(
                client_id="canonical-test",
                source_id="living_room_voice",
                user_id="resident_one",
                defer_audible_start=False,
                sleep_timer_seconds=60,
            )
            execution.sleep_timer(
                client_id="canonical-test",
                source_id="living_room_voice",
                duration_seconds=60,
            )

        self.assertEqual(
            start_audiobook.call_args.kwargs,
            {
                "client_id": "canonical-test",
                "source_id": "living_room_voice",
                "user_id": "resident_one",
                "defer_audible_start": False,
                "sleep_timer_seconds": 60,
                "audiobook_execution": audiobook_execution,
            },
        )
        self.assertEqual(
            set_timer.call_args.kwargs,
            {
                "client_id": "canonical-test",
                "source_id": "living_room_voice",
                "duration_seconds": 60,
                "audiobook_execution": audiobook_execution,
            },
        )

    def test_canonical_waiting_run_fails_closed_after_revision_change(self) -> None:
        effective = self._effective_config(enabled=True)
        execution = CanonicalRoutineExecution(
            settings=RoutineRuntimeSettings.from_effective_config(effective),
            home_assistant=HomeAssistantRuntimeSettings.from_effective_config(effective),
            audiobooks=CanonicalAudiobookExecution(
                AudiobookRuntimeSettings.from_effective_config(effective),
                satellite_control_timeout_seconds=6,
            ),
        )
        successful = lambda **_kwargs: {"ok": True, "status": "executed"}
        execution.adapters = MappingProxyType({key: successful for key in execution.adapters})

        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "memory.sqlite3"
            run = execution.start(
                "bedtime",
                client_id="canonical-test",
                inputs={"sleep_minutes": 1},
                db_path=db_path,
            )
            self.assertEqual(run["status"], "waiting")

            resumed = resume_due_routines(
                now=datetime.now(timezone.utc) + timedelta(minutes=2),
                db_path=db_path,
                adapters=execution.adapters,
                required_config_revision="oracle-config-v1:sha256:different",
            )

        self.assertEqual(resumed[0]["status"], "failed")
        self.assertIn("revision", resumed[0]["summary"].lower())

    def _effective_config(
        self,
        *,
        enabled: bool = False,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "routines.yaml"
            projection_ids: dict[str, str] = {}
            if not include_role:
                role_path.unlink()
            elif enabled:
                self._write_enabled_bundle(bundle)
                projection_ids["living_room_satellite"] = PROJECTION_ACTIVATION_ID
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
                satellite_projection_activation_ids=MappingProxyType(projection_ids),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=1,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_bundle(bundle: Path) -> None:
        household_path = bundle / "household.yaml"
        household = __import__("yaml").safe_load(household_path.read_text(encoding="utf-8"))
        household["users"][0]["capabilities"] = {
            "audiobooks": {
                "enabled": True,
                "account_id": "primary",
                "credential_secret": "RESIDENT_AUDIOBOOK_TOKEN",
            }
        }
        household["sources"] = [
            {
                "id": "living_room_voice",
                "enabled": True,
                "type": "satellite",
                "fixed": True,
                "associated_room_id": "living_room",
            }
        ]
        household_path.write_text(json.dumps(household), encoding="utf-8")

        satellites = {
            "satellites": [
                {
                    "id": "living_room_satellite",
                    "enabled": True,
                    "source_id": "living_room_voice",
                    "platform": "linux",
                    "capabilities": {
                        "voice": True,
                        "display": False,
                        "music_playback": False,
                        "audiobook_playback": True,
                    },
                    "brain_client": {
                        "base_url": "http://brain.invalid:8011",
                        "credential_secret": "BRAIN_CREDENTIAL",
                    },
                    "control_service": {
                        "base_url": "http://satellite.invalid:8021",
                        "local_client_url": "http://127.0.0.1:8021",
                        "credential_secret": "CONTROL_CREDENTIAL",
                    },
                    "enrollment": {"credential_secret": "ENROLLMENT_CREDENTIAL"},
                    "audio": {
                        "input": {"type": "system_default"},
                        "interaction_output": {"type": "system_default"},
                        "playback": {"adapter": "oracle_native"},
                    },
                    "wake": {
                        "enabled": True,
                        "model": {"format": "onnx", "asset_id": "oracle_wake"},
                    },
                }
            ]
        }
        (bundle / "satellites.yaml").write_text(json.dumps(satellites), encoding="utf-8")

        audiobooks = {
            "enabled": True,
            "provider": "primary",
            "providers": {
                "primary": {
                    "type": "audiobookshelf",
                    "base_url": "http://audiobooks.invalid",
                    "library_id": "main",
                }
            },
            "playback": {"source_ids": ["living_room_voice"]},
        }
        (bundle / "domains" / "audiobooks.yaml").write_text(json.dumps(audiobooks), encoding="utf-8")

        home_assistant = {
            "enabled": True,
            "provider": "primary",
            "providers": {
                "primary": {
                    "type": "home_assistant",
                    "base_url": "http://home-assistant.invalid:8123",
                    "credential_secret": "HOME_ASSISTANT_TOKEN",
                }
            },
            "mappings": {
                "lights_off": {
                    "kind": "action",
                    "oracle_id": "living_room_lights_off",
                    "entity_id": "light.living_room",
                    "allowed_operations": ["turn_off"],
                },
                "lights_on": {
                    "kind": "action",
                    "oracle_id": "living_room_lights_on",
                    "entity_id": "light.living_room",
                    "allowed_operations": ["turn_on"],
                },
                "lights_state": {
                    "kind": "entity",
                    "oracle_id": "living_room_lights",
                    "entity_id": "light.living_room",
                    "allowed_operations": ["read"],
                },
            },
            "automations": [],
        }
        (bundle / "domains" / "home-assistant.yaml").write_text(
            json.dumps(home_assistant), encoding="utf-8"
        )

        steps = [
            {
                "id": "lights_off",
                "type": "ui_action",
                "label": "Turn off the lights",
                "required": True,
                "on_failure": "stop",
                "action_id": "lights_off",
            },
            {
                "id": "start_book",
                "type": "audiobook_start",
                "label": "Start the audiobook",
                "required": True,
                "on_failure": "stop",
                "source_id": "living_room_voice",
                "user_id": "resident_one",
            },
            {
                "id": "sleep_timer",
                "type": "sleep_timer",
                "label": "Set the sleep timer",
                "required": True,
                "on_failure": "stop",
                "source_id": "living_room_voice",
                "duration_input": "sleep_minutes",
                "duration_unit": "minutes",
            },
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "required": True,
                "on_failure": "stop",
                "duration_input": "sleep_minutes",
                "duration_unit": "minutes",
                "max_lateness_seconds": 600,
            },
            {
                "id": "check_lights",
                "type": "state_check",
                "label": "Check the lights",
                "required": False,
                "on_failure": "continue",
                "check_id": "lights_state",
                "expected_state": "off",
                "remediation_action_id": "lights_on",
            },
            {
                "id": "check_playback",
                "type": "playback_check",
                "label": "Check playback",
                "required": False,
                "on_failure": "continue",
                "source_id": "living_room_voice",
                "check_id": "routine_audiobook_stopped",
                "remediation_action_id": "stop_audiobook",
            },
        ]
        routines = {
            "enabled": True,
            "definitions": [
                {
                    "id": "bedtime",
                    "display_name": "Bedtime",
                    "description": "Prepare the room and play an audiobook for a bounded time.",
                    "enabled": True,
                    "user_id": "resident_one",
                    "source_ids": ["living_room_voice"],
                    "triggers": {
                        "ui": True,
                        "voice": True,
                        "source_phrases": ["bedtime please"],
                        "global_phrases": ["start bedtime"],
                    },
                    "inputs": {
                        "sleep_minutes": {
                            "type": "integer",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 120,
                        }
                    },
                    "steps": steps,
                }
            ],
        }
        (bundle / "domains" / "routines.yaml").write_text(json.dumps(routines), encoding="utf-8")
        (bundle / "secrets.env").write_text(
            "BRAIN_CREDENTIAL=brain-secret-value\n"
            "CONTROL_CREDENTIAL=control-secret-value\n"
            "ENROLLMENT_CREDENTIAL=enrollment-secret-value\n"
            "RESIDENT_AUDIOBOOK_TOKEN=resident-audiobook-token\n"
            "HOME_ASSISTANT_TOKEN=home-assistant-token\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
