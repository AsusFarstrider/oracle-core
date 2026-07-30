from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import rfc8785
import oracle_satellite_projection as projection_store

from oracle_satellite_projection import (
    SatelliteProjectionCompatibilityError,
    SatelliteProjectionIntegrityError,
    SatelliteProjectionLocalStore,
)


SATELLITE_ID = "living_room_satellite"


class SatelliteProjectionLocalStoreTests(unittest.TestCase):
    def test_windows_directories_inherit_deployment_acl_while_posix_stays_private(self) -> None:
        with patch.object(projection_store.os, "name", "nt"):
            self.assertEqual(projection_store._private_directory_mode(), 0o777)
        with patch.object(projection_store.os, "name", "posix"):
            self.assertEqual(projection_store._private_directory_mode(), 0o700)

    def test_installs_separate_pair_under_brain_activation_and_restarts_offline(self) -> None:
        compatibility = self._compatibility()
        response = self._response(compatibility=compatibility, secret="directional-token")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "projection-store"
            store = SatelliteProjectionLocalStore(
                root,
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            installed = store.install(response)

            self.assertEqual(installed.activation_id, "sat_activation_" + "a" * 32)
            self.assertEqual(installed.selection_revision, 7)
            self.assertEqual(installed.restart_required_activation_id, installed.activation_id)
            self.assertEqual(installed.resolve_secret("LIVING_BRAIN_TOKEN"), "directional-token")
            self.assertNotIn("directional-token", repr(installed))
            with self.assertRaises(TypeError):
                installed.projection["source_id"] = "mutated"  # type: ignore[index]
            activation_root = root / "activations" / installed.activation_id
            self.assertTrue((activation_root / "projection.json").is_file())
            self.assertTrue((activation_root / "secrets.json").is_file())
            self.assertNotIn(b"directional-token", (activation_root / "projection.json").read_bytes())
            self.assertIn(b"directional-token", (activation_root / "secrets.json").read_bytes())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE((activation_root / "secrets.json").stat().st_mode), 0o600)

            cleared = store.mark_restarted()
            self.assertIsNone(cleared.restart_required_activation_id)
            self.assertEqual(store.mark_restarted(), cleared)

            restarted = SatelliteProjectionLocalStore(
                root,
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            ).load_selected()
            self.assertEqual(restarted, cleared)
            self.assertEqual(json.loads((root / "selected.json").read_bytes())["activation_id"], installed.activation_id)

    def test_rejects_tampering_wrong_runtime_and_nonminimal_secrets(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "canonical JSON"):
                store.install(json.dumps(json.loads(self._response(compatibility=compatibility))).encode("utf-8"))

            payload = json.loads(self._response(compatibility=compatibility))
            payload["projection"]["payload"]["source_id"] = "different_source"
            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "revision"):
                store.install(rfc8785.dumps(payload))

            incompatible = self._compatibility()
            incompatible["interaction_runtime"]["runtime_version"] = "different"
            with self.assertRaises(SatelliteProjectionCompatibilityError):
                store.install(self._response(compatibility=incompatible))

            payload = json.loads(self._response(compatibility=compatibility))
            payload["local_secrets"]["values"]["UNRELATED_TOKEN"] = "extra"
            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "minimal and complete"):
                store.install(rfc8785.dumps(payload))

    def test_selection_is_monotonic_and_same_activation_cannot_be_equivocated(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            first = store.install(self._response(compatibility=compatibility, revision=7))
            self.assertEqual(store.install(self._response(compatibility=compatibility, revision=7)), first)

            reselected = store.install(
                self._response(
                    compatibility=compatibility,
                    revision=8,
                    operation_id="selection_op_" + "8" * 32,
                )
            )
            self.assertEqual(reselected.activation, first.activation)
            self.assertEqual(reselected.selection_revision, 8)

            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "older"):
                store.install(self._response(compatibility=compatibility, revision=7))
            self.assertEqual(store.load_selected(), reselected)

            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "conflicts"):
                store.install(
                    self._response(
                        compatibility=compatibility,
                        revision=8,
                        operation_id="selection_op_" + "9" * 32,
                    )
                )
            self.assertEqual(store.load_selected(), reselected)

            with self.assertRaisesRegex(SatelliteProjectionIntegrityError, "conflicting content"):
                store.install(
                    self._response(
                        compatibility=compatibility,
                        revision=9,
                        operation_id="selection_op_" + "9" * 32,
                        secret="rotated-with-reused-activation",
                    )
                )
            self.assertEqual(store.load_selected(), reselected)

    def test_failed_pointer_replacement_preserves_previous_selection(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            first = store.install(self._response(compatibility=compatibility, revision=7))
            with patch("oracle_satellite_projection._atomic_replace", side_effect=OSError("power loss")):
                with self.assertRaisesRegex(OSError, "power loss"):
                    store.install(
                        self._response(
                            compatibility=compatibility,
                            revision=8,
                            operation_id="selection_op_" + "8" * 32,
                        )
                    )
            self.assertEqual(store.load_selected(), first)

    @staticmethod
    def _compatibility() -> dict[str, object]:
        return {
            "platform": "linux",
            "projection_schema_versions": [1],
            "interaction_runtime": {
                "runtime_version": "test-interaction-1",
                "voice_capture": False,
                "brain_interaction": False,
                "conversational_audio": False,
                "wake_processing": False,
                "cues": False,
                "audio_input_types": [],
                "interaction_output_types": [],
                "wake_model_formats": [],
            },
            "control_service": {
                "runtime_version": "test-control-1",
                "playback_authority_schema_versions": [],
                "oracle_native_music": False,
                "oracle_audiobook": False,
                "volume_control_types": [],
            },
        }

    @staticmethod
    def _response(
        *,
        compatibility: dict[str, object],
        revision: int = 7,
        operation_id: str = "selection_op_" + "7" * 32,
        secret: str = "directional-token",
    ) -> bytes:
        projection_without_revision = {
            "kind": "oracle-satellite-projection",
            "projection_schema_version": 1,
            "satellite_id": SATELLITE_ID,
            "source_id": "living_room_source",
            "runtime_compatibility": compatibility,
            "configuration": {
                "brain_client": {
                    "base_url": "http://brain.example:8011",
                    "credential_secret": "LIVING_BRAIN_TOKEN",
                },
                "interaction_runtime": None,
                "control_service": None,
            },
        }
        projection = {
            **projection_without_revision,
            "projection_revision": "oracle-projection-v1:sha256:"
            + hashlib.sha256(rfc8785.dumps(projection_without_revision)).hexdigest(),
        }
        return rfc8785.dumps(
            {
                "format": "oracle-satellite-projection-pull-v1",
                "satellite_id": SATELLITE_ID,
                "selection": {"operation_id": operation_id, "revision": revision},
                "activation": {
                    "activation_id": "sat_activation_" + "a" * 32,
                    "source_config_revision": "oracle-config-v1:sha256:" + "c" * 64,
                },
                "projection": {
                    "generation_id": "sat_projection_" + "b" * 32,
                    "payload": projection,
                },
                "local_secrets": {
                    "generation_id": "sat_secret_" + "d" * 32,
                    "values": {"LIVING_BRAIN_TOKEN": secret},
                },
            }
        )


if __name__ == "__main__":
    unittest.main()
