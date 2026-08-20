from __future__ import annotations

from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from urllib import error as urlerror

import rfc8785

from oracle_satellite_projection import SatelliteProjectionLocalStore
from oracle_satellite_projection_sync import (
    MAX_PULL_RESPONSE_BYTES,
    SYNC_TIMEOUT_SECONDS,
    EXIT_RESTART_REQUIRED,
    EXIT_FAILURE,
    SatelliteProjectionBootstrapRequired,
    SatelliteProjectionSyncError,
    SatelliteProjectionSyncResult,
    _load_enrollment_credential,
    main,
    provision_satellite_projection,
    sync_satellite_projection,
)


SATELLITE_ID = "living_room_satellite"


class _Response:
    def __init__(self, body: bytes, *, content_type: str = "application/json", cache_control: str = "no-store") -> None:
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Cache-Control"] = cache_control

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.body[:size]


class _Opener:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def __call__(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class SatelliteProjectionSyncTests(unittest.TestCase):
    def test_uses_selected_brain_edge_and_installs_changed_activation(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7, activation_character="a", secret="old-token"))
            store.mark_restarted()
            opener = _Opener(
                _Response(
                    self._response(
                        compatibility,
                        revision=8,
                        operation_character="8",
                        activation_character="b",
                        secret="new-token",
                    )
                )
            )

            result = sync_satellite_projection(store, open_url=opener)

            self.assertEqual(result.status, "activation_changed")
            self.assertTrue(result.activation_changed)
            self.assertTrue(result.selection_changed)
            self.assertTrue(result.restart_required)
            self.assertEqual(result.previous_activation_id, "sat_activation_" + "a" * 32)
            self.assertEqual(result.activation_id, "sat_activation_" + "b" * 32)
            request, timeout = opener.calls[0]
            self.assertEqual(
                request.full_url,
                "http://brain.example:8011/api/satellite/projection/living_room_satellite",
            )
            self.assertEqual(request.get_header("Authorization"), "Bearer old-token")
            self.assertEqual(request.get_header("Accept"), "application/json")
            self.assertEqual(timeout, SYNC_TIMEOUT_SECONDS)
            self.assertEqual(store.load_selected().resolve_secret("LIVING_BRAIN_TOKEN"), "new-token")

    def test_same_activation_new_selection_does_not_request_restart(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7))
            store.mark_restarted()
            opener = _Opener(
                _Response(self._response(compatibility, revision=8, operation_character="8"))
            )

            result = sync_satellite_projection(store, open_url=opener)

            self.assertEqual(result.status, "selection_updated")
            self.assertFalse(result.activation_changed)
            self.assertTrue(result.selection_changed)
            self.assertFalse(result.restart_required)

    def test_lightweight_activation_reusing_payload_does_not_request_restart(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7, activation_character="a"))
            store.mark_restarted()
            opener = _Opener(
                _Response(
                    self._response(
                        compatibility,
                        revision=8,
                        operation_character="8",
                        activation_character="b",
                        generation_character="a",
                    )
                )
            )

            result = sync_satellite_projection(store, open_url=opener)

            self.assertTrue(result.activation_changed)
            self.assertFalse(result.restart_required)
            self.assertEqual(result.status, "activation_changed")

    def test_pending_restart_latch_follows_lightweight_reselection(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7, activation_character="a"))
            opener = _Opener(
                _Response(
                    self._response(
                        compatibility,
                        revision=8,
                        operation_character="8",
                        activation_character="b",
                        generation_character="a",
                    )
                )
            )

            result = sync_satellite_projection(store, open_url=opener)

            self.assertTrue(result.restart_required)
            self.assertEqual(
                store.load_selected().restart_required_activation_id,
                "sat_activation_" + "b" * 32,
            )

    def test_ordinary_refresh_requires_prior_activation(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            opener = _Opener(_Response(b"{}"))
            with self.assertRaises(SatelliteProjectionBootstrapRequired):
                sync_satellite_projection(store, open_url=opener)
            self.assertEqual(opener.calls, [])

    def test_first_contact_uses_enrollment_boundary_once_then_installs_operational_edge(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            opener = _Opener(
                _Response(
                    self._response(
                        compatibility,
                        revision=7,
                        secret="operational-token",
                    )
                )
            )

            result = provision_satellite_projection(
                store,
                brain_bootstrap_url="http://bootstrap.example:8011",
                enrollment_credential="enrollment-token",
                open_url=opener,
            )

            self.assertEqual(result.status, "provisioned")
            self.assertTrue(result.restart_required)
            request, timeout = opener.calls[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(
                request.full_url,
                "http://bootstrap.example:8011/api/satellite/enrollment/living_room_satellite",
            )
            self.assertEqual(request.get_header("Authorization"), "Bearer enrollment-token")
            self.assertEqual(timeout, SYNC_TIMEOUT_SECONDS)
            selected = store.load_selected()
            self.assertEqual(
                selected.resolve_secret("LIVING_BRAIN_TOKEN"),
                "operational-token",
            )
            self.assertNotIn("enrollment-token", repr(selected))
            with self.assertRaisesRegex(SatelliteProjectionSyncError, "after a local activation"):
                provision_satellite_projection(
                    store,
                    brain_bootstrap_url="http://bootstrap.example:8011",
                    enrollment_credential="enrollment-token",
                    open_url=opener,
                )

    def test_enrollment_credential_file_is_restricted_and_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "enrollment.secret"
            path.write_text("enrollment-token\n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(_load_enrollment_credential(path), "enrollment-token")

            path.chmod(0o644)
            if os.name != "nt":
                with self.assertRaisesRegex(SatelliteProjectionSyncError, "permissions"):
                    _load_enrollment_credential(path)

    def test_transport_failures_and_invalid_boundaries_are_sanitized(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            store = SatelliteProjectionLocalStore(
                Path(temporary) / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7, secret="never-report-me"))
            cases = (
                _Opener(urlerror.URLError("never-report-me")),
                _Opener(_Response(b"{}", content_type="text/plain")),
                _Opener(_Response(b"{}", cache_control="public")),
                _Opener(_Response(b"x" * (MAX_PULL_RESPONSE_BYTES + 1))),
            )
            for opener in cases:
                with self.subTest(result=type(opener.result).__name__):
                    with self.assertRaises(SatelliteProjectionSyncError) as raised:
                        sync_satellite_projection(store, open_url=opener)
                    self.assertNotIn("never-report-me", str(raised.exception))
            self.assertEqual(store.load_selected().resolve_secret("LIVING_BRAIN_TOKEN"), "never-report-me")

    def test_cli_uses_fixed_change_and_failure_exit_semantics(self) -> None:
        compatibility = self._compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compatibility_path = root / "runtime-compatibility.json"
            compatibility_path.write_text(json.dumps(compatibility), encoding="utf-8")
            argv = [
                "--satellite-id",
                SATELLITE_ID,
                "--store-root",
                str(root / "store"),
                "--runtime-compatibility",
                str(compatibility_path),
            ]
            changed = SatelliteProjectionSyncResult(
                status="activation_changed",
                satellite_id=SATELLITE_ID,
                activation_id="sat_activation_" + "b" * 32,
                previous_activation_id="sat_activation_" + "a" * 32,
                selection_operation_id="selection_op_" + "8" * 32,
                selection_revision=8,
                activation_changed=True,
                selection_changed=True,
                restart_required=True,
            )
            output = io.StringIO()
            with patch("oracle_satellite_projection_sync.sync_satellite_projection", return_value=changed):
                with redirect_stdout(output):
                    self.assertEqual(main(argv), EXIT_RESTART_REQUIRED)
            self.assertTrue(json.loads(output.getvalue())["activation_changed"])

            store = SatelliteProjectionLocalStore(
                root / "store",
                satellite_id=SATELLITE_ID,
                runtime_compatibility=compatibility,
            )
            store.install(self._response(compatibility, revision=7))
            marked = io.StringIO()
            with redirect_stdout(marked):
                self.assertEqual(main([*argv, "--mark-restarted"]), 0)
            self.assertFalse(json.loads(marked.getvalue())["restart_required"])
            self.assertIsNone(store.load_selected().restart_required_activation_id)

            empty_argv = [*argv]
            empty_argv[empty_argv.index(str(root / "store"))] = str(root / "empty-store")
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(main(empty_argv), EXIT_FAILURE)
            self.assertEqual(json.loads(error.getvalue())["code"], "projection_bootstrap_required")

            credential_path = root / "enrollment.secret"
            credential_path.write_text("enrollment-token\n", encoding="utf-8")
            credential_path.chmod(0o600)
            provisioned = SatelliteProjectionSyncResult(
                status="provisioned",
                satellite_id=SATELLITE_ID,
                activation_id="sat_activation_" + "b" * 32,
                previous_activation_id="",
                selection_operation_id="selection_op_" + "8" * 32,
                selection_revision=8,
                activation_changed=True,
                selection_changed=True,
                restart_required=True,
            )
            provision_output = io.StringIO()
            with patch(
                "oracle_satellite_projection_sync.provision_satellite_projection",
                return_value=provisioned,
            ) as mock_provision:
                with redirect_stdout(provision_output):
                    self.assertEqual(
                        main(
                            [
                                *empty_argv,
                                "--brain-bootstrap-url",
                                "http://bootstrap.example:8011",
                                "--enrollment-credential-file",
                                str(credential_path),
                            ]
                        ),
                        EXIT_RESTART_REQUIRED,
                    )
            self.assertEqual(
                mock_provision.call_args.kwargs["enrollment_credential"],
                "enrollment-token",
            )
            self.assertNotIn("enrollment-token", provision_output.getvalue())

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
        compatibility: dict[str, object],
        *,
        revision: int,
        operation_character: str = "7",
        activation_character: str = "a",
        generation_character: str | None = None,
        secret: str = "old-token",
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
        generation_character = generation_character or activation_character
        return rfc8785.dumps(
            {
                "format": "oracle-satellite-projection-pull-v1",
                "satellite_id": SATELLITE_ID,
                "selection": {
                    "operation_id": "selection_op_" + operation_character * 32,
                    "revision": revision,
                },
                "activation": {
                    "activation_id": "sat_activation_" + activation_character * 32,
                    "source_config_revision": "oracle-config-v2:sha256:" + "c" * 64,
                },
                "projection": {
                    "generation_id": "sat_projection_" + generation_character * 32,
                    "payload": projection,
                },
                "local_secrets": {
                    "generation_id": "sat_secret_" + generation_character * 32,
                    "values": {"LIVING_BRAIN_TOKEN": secret},
                },
            }
        )


if __name__ == "__main__":
    unittest.main()
