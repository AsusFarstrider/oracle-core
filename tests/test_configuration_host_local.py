from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

from oracle_app.configuration import (
    ConfigurationService,
    GenerationStore,
    HOST_LOCAL_PROTOCOL_FORMAT,
    HostLocalConfigurationClient,
    HostLocalConfigurationServer,
    HostLocalDispatcher,
    HostLocalProtocolError,
    HostLocalServiceAlreadyRunning,
    SelectionCommittedAuditPending,
    candidate_role_text,
    snapshot_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and os.name != "nt", "Unix-domain socket required")
class ConfigurationHostLocalTests(unittest.TestCase):
    def test_socket_is_filesystem_protected_and_presence_locked(self) -> None:
        with self._environment() as (bundle, store, service, socket_path):
            self._activate(bundle, service)
            with self._server(socket_path, service):
                self.assertTrue(stat.S_ISSOCK(socket_path.stat().st_mode))
                self.assertEqual(stat.S_IMODE(socket_path.stat().st_mode), 0o600)
                response = HostLocalConfigurationClient(socket_path).request({"operation": "status"})
                self.assertTrue(response["ok"])
                self.assertNotIn("token", json.dumps(response).lower())
                with self.assertRaises(HostLocalServiceAlreadyRunning):
                    HostLocalConfigurationServer(bundle.parent / "other.sock", service)
            self.assertFalse(socket_path.exists())
            self.assertTrue((store.root / ".service.lock").is_file())

    def test_review_and_managed_apply_cross_the_socket_as_fixed_role_text(self) -> None:
        with self._environment() as (bundle, store, service, socket_path):
            initial = self._activate(bundle, service)
            candidate = self._candidate_copy(bundle)
            self._replace(candidate / "brain.yaml", "level: INFO", "level: DEBUG")
            roles, candidate_revision = candidate_role_text(candidate)
            client = HostLocalConfigurationClient(socket_path)
            with self._server(socket_path, service):
                review = client.request(
                    {
                        "operation": "review_candidate",
                        "roles": roles,
                        "candidate_authored_revision": candidate_revision,
                    }
                )
                self.assertTrue(review["ok"])
                self.assertTrue(review["result"]["activation_eligible"])
                self.assertEqual(review["result"]["semantic_changes"][0]["path"], "roles.brain.yaml.logging.level")

                applied = client.request(
                    {
                        "operation": "replace_authored_candidate",
                        "roles": roles,
                        "candidate_authored_revision": candidate_revision,
                        "expected_authored_revision": snapshot_candidate(bundle).authored_revision,
                        "expected_secret_generation_id": initial.selected.secrets.generation_id,
                        "acknowledgements": [],
                    }
                )
                activated = client.request(
                    {
                        "operation": "activate_candidate",
                        "roles": roles,
                        "candidate_authored_revision": candidate_revision,
                        "expected_secret_generation_id": initial.selected.secrets.generation_id,
                        "acknowledgements": [],
                    }
                )
                rolled_back = client.request(
                    {
                        "operation": "rollback",
                        "config_generation_id": initial.selected.config.generation_id,
                        "expected_secret_generation_id": initial.selected.secrets.generation_id,
                        "acknowledgements": [],
                    }
                )
                recovered = client.request({"operation": "recover"})
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["result"]["outcome"], "activated")
            self.assertTrue(activated["ok"])
            self.assertEqual(activated["result"]["outcome"], "no_op")
            self.assertTrue(rolled_back["ok"])
            self.assertEqual(rolled_back["result"]["config_revision"], initial.selected.config.config_revision)
            self.assertEqual(recovered["result"]["authoring_transaction_ids"], [])
            self.assertIn("level: DEBUG", (bundle / "brain.yaml").read_text(encoding="utf-8"))
            self.assertEqual(store.load_selected().config.config_revision, initial.selected.config.config_revision)

    def test_secret_value_is_write_only_and_never_accepted_as_an_untyped_field(self) -> None:
        with self._environment() as (bundle, _store, service, socket_path):
            initial = self._activate(bundle, service)
            raw_value = "raw-host-local-value"
            client = HostLocalConfigurationClient(socket_path)
            with self._server(socket_path, service):
                rejected = client.request(
                    {
                        "operation": "status",
                        "value": raw_value,
                    }
                )
                created = client.request(
                    {
                        "operation": "mutate_secret",
                        "secret_operation": "create_secret",
                        "logical_id": "HOST_LOCAL_TEST_TOKEN",
                        "value": raw_value,
                        "expected_secret_generation_id": initial.selected.secrets.generation_id,
                    }
                )
            self.assertFalse(rejected["ok"])
            self.assertTrue(created["ok"])
            self.assertNotIn(raw_value, json.dumps(created))
            self.assertEqual((bundle / "secrets.env").read_text(encoding="utf-8"), f"HOST_LOCAL_TEST_TOKEN={raw_value}\n")

    def test_candidate_payload_rejects_unknown_roles_and_revision_mismatch(self) -> None:
        with self._environment() as (bundle, _store, service, socket_path):
            self._activate(bundle, service)
            roles, revision = candidate_role_text(bundle)
            client = HostLocalConfigurationClient(socket_path)
            with self._server(socket_path, service):
                unknown = client.request(
                    {
                        "operation": "review_candidate",
                        "roles": {**roles, "domains/custom.yaml": "enabled: false\n"},
                        "candidate_authored_revision": revision,
                    }
                )
                mismatch = client.request(
                    {
                        "operation": "review_candidate",
                        "roles": roles,
                        "candidate_authored_revision": "oracle-authored-v1:sha256:" + "0" * 64,
                    }
                )
            self.assertEqual(unknown["error"]["code"], "request_rejected")
            self.assertEqual(mismatch["error"]["code"], "authored_revision_conflict")
            (bundle / "secrets.env").write_text("LOCAL_ONLY=value\n", encoding="utf-8")
            with self.assertRaises(HostLocalProtocolError):
                candidate_role_text(bundle)

    def test_cli_uses_socket_and_reads_secret_from_stdin_not_argv(self) -> None:
        with self._environment() as (bundle, _store, service, socket_path):
            initial = self._activate(bundle, service)
            raw_value = "stdin-only-secret"
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "oracle-config.py"),
                "--socket",
                str(socket_path),
                "secret",
                "create_secret",
                "CLI_STDIN_TOKEN",
                "--expected-secret-generation",
                initial.selected.secrets.generation_id,
                "--value-stdin",
            ]
            self.assertNotIn(raw_value, command)
            with self._server(socket_path, service):
                completed = subprocess.run(
                    command,
                    input=raw_value + "\n",
                    text=True,
                    capture_output=True,
                    cwd=REPO_ROOT,
                    check=False,
                )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn(raw_value, completed.stdout)
            self.assertNotIn(raw_value, completed.stderr)
            self.assertIn("CLI_STDIN_TOKEN", (bundle / "secrets.env").read_text(encoding="utf-8"))

    def test_cli_accepts_typed_runtime_compatibility_without_exposing_capabilities(self) -> None:
        with self._environment() as (bundle, store, service, socket_path):
            report_path = bundle.parent / "runtime-compatibility.json"
            report_path.write_text(
                json.dumps(self._compatibility_report()),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "oracle-config.py"),
                "--socket",
                str(socket_path),
                "compatibility",
                "accept",
                "--satellite-id",
                "test_satellite_alpha",
                "--report",
                str(report_path),
            ]
            with self._server(socket_path, service):
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    cwd=REPO_ROOT,
                    check=False,
                )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual(result["satellite_id"], "test_satellite_alpha")
            self.assertEqual(result["platform"], "linux")
            self.assertEqual(result["interaction_runtime_version"], "stage3-test")
            self.assertNotIn("audio_input_types", completed.stdout)
            self.assertTrue(
                (
                    store.root
                    / "runtime-compatibility"
                    / "test_satellite_alpha.json"
                ).is_file()
            )

    def test_offline_cli_activates_actual_candidate_with_companion_without_initializing_store(self) -> None:
        with self._environment() as (bundle, store, _service, _socket_path):
            raw_value = "offline-bootstrap-secret"
            (bundle / "secrets.env").write_text(f"OFFLINE_BOOTSTRAP_TOKEN={raw_value}\n", encoding="utf-8")
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "oracle-config.py"),
                "--offline-store",
                str(store.root),
                "--authoring-root",
                str(bundle),
                "activate",
                "--candidate",
                str(bundle),
            ]

            completed = subprocess.run(command, text=True, capture_output=True, cwd=REPO_ROOT, check=False)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn(raw_value, completed.stdout)
            self.assertNotIn(raw_value, completed.stderr)
            self.assertIn("OFFLINE_BOOTSTRAP_TOKEN", store.load_selected().secrets.snapshot.present_ids)

            selected_before_drift = store.load_selected().activation
            (bundle / "secrets.env").write_text("OFFLINE_BOOTSTRAP_TOKEN=manual-drift\n", encoding="utf-8")
            drifted = subprocess.run(
                command + ["--expected-secret-generation", store.load_selected().secrets.generation_id],
                text=True,
                capture_output=True,
                cwd=REPO_ROOT,
                check=False,
            )
            self.assertEqual(drifted.returncode, 2)
            self.assertIn("explicit secret transaction", drifted.stderr)
            self.assertEqual(store.load_selected().activation, selected_before_drift)

            missing_store = store.root.parent / "missing-store"
            missing = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "oracle-config.py"),
                    "--offline-store",
                    str(missing_store),
                    "status",
                ],
                text=True,
                capture_output=True,
                cwd=REPO_ROOT,
                check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(missing_store.exists())

    def test_offline_cli_refuses_while_socket_service_holds_presence_lock(self) -> None:
        with self._environment() as (bundle, store, service, socket_path):
            self._activate(bundle, service)
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "oracle-config.py"),
                "--offline-store",
                str(store.root),
                "status",
            ]
            with self._server(socket_path, service):
                completed = subprocess.run(command, text=True, capture_output=True, cwd=REPO_ROOT, check=False)

            self.assertEqual(completed.returncode, 2)
            response = json.loads(completed.stderr)
            self.assertEqual(response["error"]["code"], "configuration_service_running")

    def test_dispatcher_sanitizes_unexpected_internal_failure(self) -> None:
        with self._environment() as (_bundle, _store, service, _socket_path):
            secret_detail = "internal-sensitive-detail"
            service.status = lambda: (_ for _ in ()).throw(RuntimeError(secret_detail))  # type: ignore[method-assign]
            response = HostLocalDispatcher(service).dispatch(
                {"format": HOST_LOCAL_PROTOCOL_FORMAT, "operation": "status"}
            )
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "internal_error")
            self.assertNotIn(secret_detail, json.dumps(response))

    def test_dispatcher_reports_committed_selection_with_pending_audit_explicitly(self) -> None:
        with self._environment() as (_bundle, _store, service, _socket_path):
            service.status = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
                SelectionCommittedAuditPending("selection_op_" + "a" * 32, 7)
            )
            response = HostLocalDispatcher(service).dispatch(
                {"format": HOST_LOCAL_PROTOCOL_FORMAT, "operation": "status"}
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "selection_committed_audit_pending")
            self.assertEqual(response["error"]["selection_revision"], 7)

    def test_regular_file_at_socket_path_is_never_unlinked(self) -> None:
        with self._environment() as (_bundle, _store, service, socket_path):
            socket_path.parent.mkdir(mode=0o700, parents=True)
            socket_path.write_text("preserve-me", encoding="utf-8")
            with self.assertRaises(HostLocalProtocolError):
                HostLocalConfigurationServer(socket_path, service)
            self.assertEqual(socket_path.read_text(encoding="utf-8"), "preserve-me")
            socket_path.unlink()
            socket_path.parent.chmod(0o770)
            with self.assertRaises(HostLocalProtocolError):
                HostLocalConfigurationServer(socket_path, service)

    def _environment(self):
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        bundle = base / "config"
        shutil.copytree(EXAMPLE_ROOT, bundle)
        (bundle / "secrets.env.example").unlink()
        store = GenerationStore(base / "installed")
        store.initialize("example-home")
        service = ConfigurationService(
            store,
            authoring_mode="managed_writable",
            authoring_root=bundle,
        )
        socket_path = base / "run" / "oracle-config.sock"

        class Environment:
            def __enter__(self):
                return bundle, store, service, socket_path

            def __exit__(self, *_args):
                temporary.cleanup()

        return Environment()

    @staticmethod
    def _server(socket_path: Path, service: ConfigurationService):
        server = HostLocalConfigurationServer(socket_path, service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)

        class RunningServer:
            def __enter__(self):
                thread.start()
                return server

            def __exit__(self, *_args):
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        return RunningServer()

    @staticmethod
    def _activate(bundle: Path, service: ConfigurationService):
        return service.activate_candidate(
            bundle,
            expected_authored_revision=snapshot_candidate(bundle).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
        )

    @staticmethod
    def _candidate_copy(bundle: Path) -> Path:
        candidate = bundle.parent / "candidate"
        shutil.copytree(bundle, candidate)
        companion = candidate / "secrets.env"
        if companion.exists():
            companion.unlink()
        return candidate

    @staticmethod
    def _replace(path: Path, before: str, after: str) -> None:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(before, after), encoding="utf-8")

    @staticmethod
    def _compatibility_report() -> dict[str, object]:
        return {
            "platform": "linux",
            "projection_schema_versions": [1],
            "interaction_runtime": {
                "runtime_version": "stage3-test",
                "voice_capture": True,
                "brain_interaction": True,
                "conversational_audio": True,
                "wake_processing": True,
                "cues": True,
                "audio_input_types": ["alsa_arecord"],
                "interaction_output_types": ["system_default"],
                "wake_model_formats": ["onnx"],
            },
            "control_service": {
                "runtime_version": "stage3-test",
                "playback_authority_schema_versions": [1],
                "oracle_native_music": True,
                "oracle_audiobook": True,
                "volume_control_types": ["alsa"],
            },
        }


if __name__ == "__main__":
    unittest.main()
