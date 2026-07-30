from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from types import MappingProxyType
import unittest

from oracle_app.configuration import (
    ControlServiceCompatibility,
    InteractionRuntimeCompatibility,
    ProjectionGenerationError,
    SatelliteRuntimeCompatibility,
    SatelliteRuntimeCompatibilityStore,
    SatelliteProjectionGenerationStore,
    SatelliteProjectionAuthenticationError,
    SATELLITE_PROJECTION_PULL_FORMAT,
    SatelliteProjectionResolver,
    SatellitesConfiguration,
    SecretSnapshot,
    generate_satellite_projection,
    load_bundle,
    normalize_bundle,
)
from oracle_app.configuration.domain_models import MusicConfiguration
from oracle_satellite_projection import SatelliteProjectionLocalStore


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
SOURCE_REVISION = f"oracle-config-v1:sha256:{'1' * 64}"


class SatelliteProjectionTests(unittest.TestCase):
    def test_projection_is_deterministic_minimal_and_secret_scoped(self) -> None:
        bundle = self._bundle()
        first = generate_satellite_projection(
            bundle,
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        rotated = generate_satellite_projection(
            bundle,
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("rotated"),
        )

        self.assertEqual(first.projection.projection_revision, rotated.projection.projection_revision)
        self.assertEqual(first.canonical_bytes, rotated.canonical_bytes)
        newer_brain_revision = generate_satellite_projection(
            bundle,
            source_config_revision=f"oracle-config-v1:sha256:{'2' * 64}",
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        self.assertEqual(first.projection.projection_revision, newer_brain_revision.projection.projection_revision)
        self.assertEqual(first.canonical_bytes, newer_brain_revision.canonical_bytes)
        self.assertEqual(
            first.required_secret_ids,
            frozenset({"LIVING_BRAIN_TOKEN", "LIVING_CONTROL_TOKEN", "PLEX_TOKEN"}),
        )
        self.assertNotIn("LIVING_ENROLLMENT_TOKEN", first.secrets.present_ids)
        self.assertNotIn(b"first", first.canonical_bytes)
        self.assertEqual(first.projection.configuration.control_service.music.provider.timeout_seconds, 8)  # type: ignore[union-attr]
        self.assertNotIn(b"living.example.invalid", first.canonical_bytes)
        self.assertNotIn("ui", first.projection.configuration.model_dump())
        self.assertEqual(
            first.projection.configuration.brain_client.credential_secret,
            "LIVING_BRAIN_TOKEN",
        )
        self.assertNotIn(
            "brain_client",
            first.projection.configuration.interaction_runtime.model_dump(),  # type: ignore[union-attr]
        )
        self.assertEqual(
            first.projection.configuration.interaction_runtime.audio.interaction_output.type,  # type: ignore[union-attr]
            "system_default",
        )
        interaction_playback = first.projection.configuration.interaction_runtime.audio.playback.model_dump()  # type: ignore[union-attr]
        self.assertNotIn("adapter", interaction_playback)
        self.assertNotIn("volume_control", interaction_playback)
        self.assertEqual(first.projection.configuration.control_service.adapter, "oracle_native")  # type: ignore[union-attr]
        self.assertEqual(
            first.projection.configuration.control_service.volume_control.type,  # type: ignore[union-attr]
            "windows_default_endpoint",
        )

    def test_runtime_incompatibility_blocks_generation(self) -> None:
        incompatible_control = self._compatibility().control_service.model_copy(update={"oracle_native_music": False})
        incompatible = self._compatibility().model_copy(update={"control_service": incompatible_control})
        with self.assertRaisesRegex(ProjectionGenerationError, "native music"):
            generate_satellite_projection(
                self._bundle(),
                source_config_revision=SOURCE_REVISION,
                satellite_id="living_room_satellite",
                runtime_compatibility=incompatible,
                secrets=self._secrets("value"),
            )

    def test_display_only_satellite_keeps_common_brain_client_for_projection_pull(self) -> None:
        bundle = self._bundle()
        satellite = bundle.satellites.satellites[0].model_copy(
            update={
                "capabilities": bundle.satellites.satellites[0].capabilities.model_copy(  # type: ignore[union-attr]
                    update={
                        "voice": False,
                        "display": True,
                        "music_playback": False,
                        "audiobook_playback": False,
                    }
                ),
                "wake": bundle.satellites.satellites[0].wake.model_copy(  # type: ignore[union-attr]
                    update={"enabled": False}
                ),
            }
        )
        roles = dict(bundle.roles)
        roles["satellites.yaml"] = SatellitesConfiguration(satellites=[satellite])
        display_bundle = replace(bundle, roles=MappingProxyType(roles))

        generated = generate_satellite_projection(
            display_bundle,
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("display"),
        )

        self.assertIsNone(generated.projection.configuration.interaction_runtime)
        self.assertIsNone(generated.projection.configuration.control_service)
        self.assertEqual(
            generated.projection.configuration.brain_client.base_url,
            "http://brain.example.invalid:8011",
        )
        self.assertEqual(generated.required_secret_ids, frozenset({"LIVING_BRAIN_TOKEN"}))

    def test_compatibility_is_checked_against_the_owning_component(self) -> None:
        incompatible_interaction = self._compatibility().interaction_runtime.model_copy(
            update={"interaction_output_types": []}
        )
        with self.assertRaisesRegex(ProjectionGenerationError, "conversational output"):
            generate_satellite_projection(
                self._bundle(),
                source_config_revision=SOURCE_REVISION,
                satellite_id="living_room_satellite",
                runtime_compatibility=self._compatibility().model_copy(
                    update={"interaction_runtime": incompatible_interaction}
                ),
                secrets=self._secrets("value"),
            )

        incompatible_control = self._compatibility().control_service.model_copy(
            update={"volume_control_types": []}
        )
        with self.assertRaisesRegex(ProjectionGenerationError, "Control service.*volume-control"):
            generate_satellite_projection(
                self._bundle(),
                source_config_revision=SOURCE_REVISION,
                satellite_id="living_room_satellite",
                runtime_compatibility=self._compatibility().model_copy(
                    update={"control_service": incompatible_control}
                ),
                secrets=self._secrets("value"),
            )

    def test_missing_local_secret_blocks_without_exposing_identity(self) -> None:
        with self.assertRaisesRegex(ProjectionGenerationError, "missing required"):
            generate_satellite_projection(
                self._bundle(),
                source_config_revision=SOURCE_REVISION,
                satellite_id="living_room_satellite",
                runtime_compatibility=self._compatibility(),
                secrets=SecretSnapshot(),
            )

    def test_last_accepted_compatibility_report_is_durable_without_freshness_expiry(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationStore

        with tempfile.TemporaryDirectory() as temporary:
            generation_store = GenerationStore(Path(temporary) / "store")
            generation_store.initialize("example-home")
            reports = SatelliteRuntimeCompatibilityStore(generation_store)
            first = reports.accept(
                "living_room_satellite",
                self._compatibility(),
                accepted_at="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(reports.load("living_room_satellite"), first)

            upgraded_interaction = self._compatibility().interaction_runtime.model_copy(update={"runtime_version": "2.0.0"})
            upgraded = self._compatibility().model_copy(update={"interaction_runtime": upgraded_interaction})
            reports.accept("living_room_satellite", upgraded, accepted_at="2026-07-13T00:00:00+00:00")
            self.assertEqual(reports.load("living_room_satellite").report.interaction_runtime.runtime_version, "2.0.0")  # type: ignore[union-attr]

    def test_projection_and_local_secrets_install_as_an_immutable_activation_pair(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationStore

        generated = generate_satellite_projection(
            self._bundle(),
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            generation_store = GenerationStore(root)
            generation_store.initialize("example-home")
            projections = SatelliteProjectionGenerationStore(generation_store)
            installed = projections.install(generated)
            reloaded = projections.load_installed(
                "living_room_satellite",
                installed.activation.generation_id,
            )

            self.assertEqual(reloaded.activation, installed.activation)
            self.assertEqual(reloaded.projection, installed.projection)
            self.assertEqual(reloaded.secrets.generation_id, installed.secrets.generation_id)
            self.assertEqual(reloaded.projection.projection_revision, generated.projection.projection_revision)
            self.assertEqual(reloaded.secrets.snapshot.present_ids, generated.required_secret_ids)
            base = root / "projections" / "living_room_satellite"
            projection_payload = base / "projection-generations" / installed.projection.generation_id / "projection.json"
            secret_payload = base / "secret-generations" / installed.secrets.generation_id / "secrets.json"
            self.assertNotIn("first", projection_payload.read_text(encoding="utf-8"))
            self.assertIn("first", secret_payload.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(secret_payload.stat().st_mode & 0o777, 0o600)

    def test_new_brain_revision_reuses_artifacts_but_selects_new_bound_activation(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationStore

        bundle = self._bundle()
        first_generated = generate_satellite_projection(
            bundle,
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        second_revision = f"oracle-config-v1:sha256:{'2' * 64}"
        second_generated = replace(first_generated, source_config_revision=second_revision)
        with tempfile.TemporaryDirectory() as temporary:
            store = GenerationStore(Path(temporary) / "store")
            store.initialize("example-home")
            projections = SatelliteProjectionGenerationStore(store)
            first = projections.install(first_generated)
            second = projections.install(second_generated)

            self.assertEqual(first.projection.generation_id, second.projection.generation_id)
            self.assertEqual(first.secrets.generation_id, second.secrets.generation_id)
            self.assertNotEqual(first.activation.generation_id, second.activation.generation_id)
            self.assertEqual(second.activation.source_config_revision, second_revision)

    def test_selected_pointer_atomically_binds_satellite_activation_map(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationIntegrityError, GenerationStore

        bundle = self._bundle()
        normalized = normalize_bundle(bundle)
        generated = generate_satellite_projection(
            bundle,
            source_config_revision=normalized.config_revision,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = GenerationStore(Path(temporary) / "store")
            store.initialize("example-home")
            config = store._install_config(normalized, required_secret_ids=generated.required_secret_ids)
            secret_generation = store.install_secrets(self._secrets("first"))
            brain_activation = store.create_activation(config.generation_id, secret_generation.generation_id)
            projections = SatelliteProjectionGenerationStore(store)
            satellite = projections.install(generated)
            selected = store._replace_selected_pointer(
                brain_activation.generation_id,
                operation_id="selection_op_" + "1" * 32,
                selection_revision=1,
                satellite_projection_activation_ids={
                    "living_room_satellite": satellite.activation.generation_id
                },
            )

            self.assertEqual(
                dict(store.load_selected().satellite_projection_activation_ids),
                dict(selected.satellite_projection_activation_ids),
            )
            resolved = SatelliteProjectionResolver(store).resolve(
                "living_room_satellite", "first-brain"
            )
            self.assertEqual(resolved.installed.activation, satellite.activation)
            self.assertEqual(resolved.selection_revision, selected.selection_revision)
            authenticated_applied = SatelliteProjectionResolver(store).authenticate_activation(
                "living_room_satellite",
                satellite.activation.generation_id,
                "first-brain",
            )
            self.assertEqual(authenticated_applied.activation, satellite.activation)
            with self.assertRaises(SatelliteProjectionAuthenticationError):
                SatelliteProjectionResolver(store).resolve("living_room_satellite", "wrong")
            with self.assertRaises(SatelliteProjectionAuthenticationError):
                SatelliteProjectionResolver(store).authenticate_activation(
                    "living_room_satellite",
                    satellite.activation.generation_id,
                    "wrong",
                )
            with self.assertRaises(SatelliteProjectionAuthenticationError):
                SatelliteProjectionResolver(store).resolve("other_satellite", "first-brain")

            enrolled = SatelliteProjectionResolver(store).resolve_enrollment(
                "living_room_satellite",
                "first-enrollment",
            )
            self.assertEqual(enrolled.installed.activation, satellite.activation)
            with self.assertRaises(SatelliteProjectionAuthenticationError):
                SatelliteProjectionResolver(store).resolve_enrollment(
                    "living_room_satellite",
                    "first-brain",
                )
            enrollment_pull = SatelliteProjectionResolver(store).resolve_enrollment_pull(
                "living_room_satellite",
                "first-enrollment",
            )
            self.assertNotIn(
                "LIVING_ENROLLMENT_TOKEN",
                enrollment_pull.to_payload()["local_secrets"]["values"],
            )

            pull = SatelliteProjectionResolver(store).resolve_pull(
                "living_room_satellite", "first-brain"
            )
            payload = pull.to_payload()
            self.assertEqual(
                set(payload),
                {"format", "satellite_id", "selection", "activation", "projection", "local_secrets"},
            )
            self.assertEqual(payload["format"], SATELLITE_PROJECTION_PULL_FORMAT)
            self.assertEqual(payload["satellite_id"], "living_room_satellite")
            self.assertEqual(
                payload["selection"],
                {
                    "operation_id": selected.selection_operation_id,
                    "revision": selected.selection_revision,
                },
            )
            self.assertEqual(
                payload["activation"],
                {
                    "activation_id": satellite.activation.generation_id,
                    "source_config_revision": normalized.config_revision,
                },
            )
            self.assertEqual(set(payload["projection"]), {"generation_id", "payload"})
            self.assertEqual(payload["projection"]["generation_id"], satellite.projection.generation_id)
            self.assertEqual(
                payload["projection"]["payload"],
                satellite.projection.projection.model_dump(mode="json"),
            )
            self.assertEqual(payload["local_secrets"]["generation_id"], satellite.secrets.generation_id)
            self.assertEqual(set(payload["local_secrets"]), {"generation_id", "values"})
            self.assertEqual(
                payload["local_secrets"]["values"],
                {
                    logical_id: satellite.secrets.snapshot.resolve(logical_id)
                    for logical_id in sorted(satellite.secrets.snapshot.present_ids)
                },
            )
            self.assertEqual(json.loads(pull.canonical_bytes()), payload)
            for secret_value in payload["local_secrets"]["values"].values():
                self.assertNotIn(secret_value, repr(pull))
            self.assertNotIn("LIVING_ENROLLMENT_TOKEN", payload["local_secrets"]["values"])
            locally_selected = SatelliteProjectionLocalStore(
                Path(temporary) / "satellite-local-store",
                satellite_id="living_room_satellite",
                runtime_compatibility=self._compatibility().model_dump(mode="json"),
            ).install(pull.canonical_bytes())
            self.assertEqual(locally_selected.activation_id, satellite.activation.generation_id)
            self.assertEqual(locally_selected.selection_revision, selected.selection_revision)
            self.assertEqual(locally_selected.secret_ids, satellite.secrets.snapshot.present_ids)
            with self.assertRaisesRegex(GenerationIntegrityError, "different Brain configuration revision"):
                store._replace_selected_pointer(
                    brain_activation.generation_id,
                    operation_id="selection_op_" + "2" * 32,
                    selection_revision=2,
                    satellite_projection_activation_ids={
                        "living_room_satellite": projections.install(
                            replace(generated, source_config_revision=f"oracle-config-v1:sha256:{'2' * 64}")
                        ).activation.generation_id
                    },
                )

    def test_projection_secret_rotation_preserves_projection_revision_with_new_activation(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationStore

        generated = generate_satellite_projection(
            self._bundle(),
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            generation_store = GenerationStore(Path(temporary) / "store")
            generation_store.initialize("example-home")
            projections = SatelliteProjectionGenerationStore(generation_store)
            first = projections.install(generated)
            rotated_snapshot = SecretSnapshot({
                logical_id: self._secrets("rotated").resolve(logical_id) or ""
                for logical_id in generated.required_secret_ids
            })
            rotated_secrets = projections.install_secrets("living_room_satellite", rotated_snapshot)
            rotated_activation = projections.create_activation(
                "living_room_satellite",
                first.projection.generation_id,
                rotated_secrets.generation_id,
                source_config_revision=SOURCE_REVISION,
            )

            self.assertNotEqual(first.activation.generation_id, rotated_activation.generation_id)
            self.assertEqual(
                projections.load_installed("living_room_satellite", rotated_activation.generation_id).projection.projection_revision,
                first.projection.projection_revision,
            )

    def test_projection_tampering_and_nonminimal_secret_pairing_fail_closed(self) -> None:
        import tempfile
        from oracle_app.configuration import GenerationIntegrityError, GenerationStore, GenerationStoreError

        generated = generate_satellite_projection(
            self._bundle(),
            source_config_revision=SOURCE_REVISION,
            satellite_id="living_room_satellite",
            runtime_compatibility=self._compatibility(),
            secrets=self._secrets("first"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            generation_store = GenerationStore(root)
            generation_store.initialize("example-home")
            projections = SatelliteProjectionGenerationStore(generation_store)
            installed = projections.install(generated)
            extra = projections.install_secrets(
                "living_room_satellite",
                SecretSnapshot({
                    **{
                        logical_id: generated.secrets.resolve(logical_id) or ""
                        for logical_id in generated.secrets.present_ids
                    },
                    "UNRELATED_TOKEN": "not-local",
                }),
            )
            with self.assertRaisesRegex(GenerationStoreError, "exactly"):
                projections.create_activation(
                    "living_room_satellite",
                    installed.projection.generation_id,
                    extra.generation_id,
                    source_config_revision=SOURCE_REVISION,
                )

            projection_directory = (
                root
                / "projections"
                / "living_room_satellite"
                / "projection-generations"
                / installed.projection.generation_id
            )
            metadata_path = projection_directory / "metadata.json"
            original_metadata = metadata_path.read_bytes()
            metadata = json.loads(original_metadata)
            metadata["required_secret_ids"] = metadata["required_secret_ids"][:-1]
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(GenerationIntegrityError, "incomplete"):
                projections.load_installed("living_room_satellite", installed.activation.generation_id)
            metadata_path.write_bytes(original_metadata)

            payload_path = projection_directory / "projection.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload["source_id"] = "tampered_source"
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(GenerationIntegrityError):
                projections.load_installed("living_room_satellite", installed.activation.generation_id)
            with self.assertRaises(GenerationIntegrityError):
                projections.load_installed("other_satellite", installed.activation.generation_id)

    @staticmethod
    def _compatibility() -> SatelliteRuntimeCompatibility:
        return SatelliteRuntimeCompatibility(
            platform="windows",
            projection_schema_versions=[1],
            interaction_runtime=InteractionRuntimeCompatibility(
                runtime_version="1.0.0",
                voice_capture=True,
                brain_interaction=True,
                conversational_audio=True,
                wake_processing=True,
                cues=True,
                audio_input_types=["system_default"],
                interaction_output_types=["system_default"],
                wake_model_formats=["onnx"],
            ),
            control_service=ControlServiceCompatibility(
                runtime_version="1.0.0",
                playback_authority_schema_versions=[1],
                oracle_native_music=True,
                oracle_audiobook=True,
                volume_control_types=["windows_default_endpoint"],
            ),
        )

    @staticmethod
    def _secrets(value: str) -> SecretSnapshot:
        return SecretSnapshot({
            "LIVING_BRAIN_TOKEN": f"{value}-brain",
            "LIVING_CONTROL_TOKEN": f"{value}-control",
            "LIVING_ENROLLMENT_TOKEN": f"{value}-enrollment",
            "PLEX_TOKEN": f"{value}-plex",
            "UNRELATED_TOKEN": f"{value}-unrelated",
        })

    @staticmethod
    def _bundle():
        bundle = load_bundle(EXAMPLE_ROOT)
        roles = dict(bundle.roles)
        roles["satellites.yaml"] = SatellitesConfiguration.model_validate({
            "satellites": [{
                "id": "living_room_satellite",
                "enabled": True,
                "source_id": "living_room_voice",
                "platform": "windows",
                "capabilities": {
                    "voice": True,
                    "display": True,
                    "music_playback": True,
                    "audiobook_playback": True,
                },
                "brain_client": {
                    "base_url": "http://brain.example.invalid:8011",
                    "credential_secret": "LIVING_BRAIN_TOKEN",
                },
                "control_service": {
                    "base_url": "http://living.example.invalid:8021",
                    "local_client_url": "http://127.0.0.1:8021",
                    "credential_secret": "LIVING_CONTROL_TOKEN",
                },
                "enrollment": {"credential_secret": "LIVING_ENROLLMENT_TOKEN"},
                "audio": {
                    "input": {"type": "system_default"},
                    "interaction_output": {"type": "system_default"},
                    "playback": {
                        "adapter": "oracle_native",
                        "volume_control": {"type": "windows_default_endpoint"},
                    },
                },
                "ui": {
                    "enabled": True,
                    "touch": True,
                    "profile": "wall_display",
                    "layout": "default",
                    "pages": ["home"],
                    "bottom_nav": ["home"],
                },
                "wake": {
                    "enabled": True,
                    "model": {"format": "onnx", "asset_id": "hey_oracle"},
                },
            }]
        })
        roles["domains/music.yaml"] = MusicConfiguration.model_validate({
            "enabled": True,
            "provider": "plex",
            "providers": {
                "plex": {
                    "type": "plex",
                    "base_url": "http://plex.example.invalid:32400",
                    "credential_secret": "PLEX_TOKEN",
                    "timeout_seconds": 8,
                    "music_section_id": 1,
                }
            },
            "matching": {},
            "playback": {"source_ids": ["living_room_voice"]},
        })
        return replace(bundle, roles=MappingProxyType(roles))


if __name__ == "__main__":
    unittest.main()
