from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import stat
import tempfile
import unittest

from oracle_app.configuration import (
    BrainConfigurationHostLocalRuntime,
    ConfigurationBootstrapError,
    ConfigurationBootstrapSettings,
    GenerationStore,
    HostLocalConfigurationClient,
    arm_runtime_cutover,
    inspect_candidate,
    load_standard_installation_effective_config,
    resolve_brain_configuration_startup,
    start_brain_configuration_host_local_runtime,
)
from oracle_app.installation import (
    ActivationRequest,
    InstallationLayout,
    publish_activation,
    select_activation,
)
from oracle_app.installation_identity import environment_directory_name


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationBootstrapSettingsTests(unittest.TestCase):
    def test_startup_requires_complete_canonical_bootstrap(self) -> None:
        with self.assertRaises(ConfigurationBootstrapError):
            resolve_brain_configuration_startup({})
        with self.assertRaises(ConfigurationBootstrapError):
            resolve_brain_configuration_startup({"ORACLE_ALLOW_LEGACY_CONFIGURATION": "1"})

    def test_complete_unarmed_bootstrap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            store = root / "store"
            bundle.mkdir()
            GenerationStore(store).initialize("example-home")
            values = self._values(bundle, store, root / "oracle.sock")
            with self.assertRaisesRegex(ConfigurationBootstrapError, "has not been armed"):
                resolve_brain_configuration_startup(values)

    def test_armed_store_selects_canonical_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            store = GenerationStore(root / "store")
            store.initialize("example-home")
            config, secret = store.install_candidate(inspect_candidate(bundle))
            activation = store.create_activation(config.generation_id, secret.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - startup boundary setup
                activation.generation_id,
                operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids={},
            )
            arm_runtime_cutover(store, store.load_selected(), actor="host_local_cli")
            values = self._values(bundle, store.root, root / "oracle.sock")

            startup = resolve_brain_configuration_startup(values)
            self.assertEqual(startup.mode, "canonical")
            self.assertIsNotNone(startup.effective_config)

    def test_standard_installation_activation_is_the_configuration_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = InstallationLayout(Path(temporary) / "oracle")
            for directory in layout.required_directories():
                directory.mkdir(parents=True, exist_ok=True)
            store = GenerationStore(layout.configuration, secret_root=layout.secrets)
            store.initialize("example-home")
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            config, secret = store.install_candidate(inspect_candidate(bundle))
            activation = store.create_activation(config.generation_id, secret.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - exact startup-boundary setup
                activation.generation_id,
                operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids={},
            )
            arm_runtime_cutover(store, store.load_selected(), actor="host_local_cli")
            request = self._installation_request(activation.generation_id)
            self._install_component_directories(layout, request)
            selected = publish_activation(layout, request)
            select_activation(layout, "active", selected)

            effective = load_standard_installation_effective_config(layout)
            self.assertEqual(effective.activation_generation_id, activation.generation_id)

            second = store.create_activation(config.generation_id, secret.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - disagreement canary
                second.generation_id,
                operation_id="selection_op_22222222222222222222222222222222",
                selection_revision=2,
                satellite_projection_activation_ids={},
            )
            with self.assertRaisesRegex(Exception, "disagrees with the complete installation activation"):
                load_standard_installation_effective_config(layout)

    @staticmethod
    def _installation_request(configuration_activation_id: str) -> ActivationRequest:
        return ActivationRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-tree-" + "2" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
            configuration_activation_identity=configuration_activation_id,
            service_definition_identity="systemd-unit-" + "5" * 64,
        )

    @staticmethod
    def _install_component_directories(layout: InstallationLayout, request: ActivationRequest) -> None:
        for path in (
            layout.revisions / request.application_revision_identity,
            layout.environments / environment_directory_name(request.python_environment_identity),
            layout.deployments / request.household_deployment_revision,
        ):
            path.mkdir()

    def test_absent_bootstrap_is_disabled_without_creating_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            settings = ConfigurationBootstrapSettings.from_environment({})
            runtime = start_brain_configuration_host_local_runtime({})
            self.assertIsNone(settings)
            self.assertFalse(runtime.enabled)
            runtime.stop()
            self.assertFalse(missing.exists())

    def test_partial_empty_and_relative_bootstrap_fail_closed(self) -> None:
        with self.assertRaises(ConfigurationBootstrapError):
            ConfigurationBootstrapSettings.from_environment({"ORACLE_CONFIG_BUNDLE_ROOT": "/tmp"})

        complete = {
            "ORACLE_CONFIG_BUNDLE_ROOT": "/tmp",
            "ORACLE_CONFIG_STORE_ROOT": "/tmp",
            "ORACLE_CONFIG_SOCKET_PATH": "/tmp/oracle.sock",
            "ORACLE_CONFIG_AUTHORING_MODE": "",
        }
        with self.assertRaises(ConfigurationBootstrapError):
            ConfigurationBootstrapSettings.from_environment(complete)
        complete["ORACLE_CONFIG_AUTHORING_MODE"] = "managed_writable"
        complete["ORACLE_CONFIG_SOCKET_PATH"] = "oracle.sock"
        with self.assertRaises(ConfigurationBootstrapError):
            ConfigurationBootstrapSettings.from_environment(complete)

    def test_bundle_store_and_socket_topology_must_remain_separate_after_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            store = root / "store"
            bundle.mkdir()
            store.mkdir()

            for candidate_store in (bundle, bundle / "installed"):
                candidate_store.mkdir(exist_ok=True)
                with self.subTest(store=str(candidate_store)):
                    with self.assertRaises(ConfigurationBootstrapError):
                        ConfigurationBootstrapSettings.from_environment(
                            self._values(bundle, candidate_store, root / "run" / "oracle.sock")
                        )

            nested_bundle = store / "bundle"
            nested_bundle.mkdir()
            with self.assertRaises(ConfigurationBootstrapError):
                ConfigurationBootstrapSettings.from_environment(
                    self._values(nested_bundle, store, root / "run" / "oracle.sock")
                )
            with self.assertRaises(ConfigurationBootstrapError):
                ConfigurationBootstrapSettings.from_environment(
                    self._values(bundle, store, bundle / "run" / "oracle.sock")
                )

            if hasattr(os, "symlink"):
                alias = root / "bundle-alias"
                alias.symlink_to(bundle, target_is_directory=True)
                store_alias = root / "store-alias"
                store_alias.symlink_to(bundle, target_is_directory=True)
                with self.assertRaises(ConfigurationBootstrapError):
                    ConfigurationBootstrapSettings.from_environment(
                        self._values(bundle, store_alias, root / "run" / "oracle.sock")
                    )
                with self.assertRaises(ConfigurationBootstrapError):
                    ConfigurationBootstrapSettings.from_environment(
                        self._values(alias, store, bundle / "oracle.sock")
                    )

    @staticmethod
    def _values(bundle: Path, store_root: Path, socket_path: Path) -> dict[str, str]:
        return {
            "ORACLE_CONFIG_BUNDLE_ROOT": str(bundle),
            "ORACLE_CONFIG_STORE_ROOT": str(store_root),
            "ORACLE_CONFIG_SOCKET_PATH": str(socket_path),
            "ORACLE_CONFIG_AUTHORING_MODE": "managed_writable",
        }


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and os.name != "nt", "Unix-domain socket required")
class ConfigurationBootstrapRuntimeTests(unittest.TestCase):
    def test_complete_bootstrap_serves_status_and_cleans_up(self) -> None:
        with self._environment() as (bundle, store_root, socket_path, environment):
            runtime = start_brain_configuration_host_local_runtime(environment)
            try:
                self.assertTrue(runtime.enabled)
                self.assertTrue(stat.S_ISSOCK(socket_path.stat().st_mode))
                self.assertEqual(stat.S_IMODE(socket_path.stat().st_mode), 0o600)
                response = HostLocalConfigurationClient(socket_path).request({"operation": "status"})
                self.assertTrue(response["ok"])
                self.assertEqual(response["result"]["authoring_mode"], "managed_writable")
                self.assertTrue(response["result"]["authoring_root_configured"])
            finally:
                runtime.stop()
            self.assertFalse(socket_path.exists())
            self.assertTrue((store_root / ".service.lock").exists())
            self.assertTrue(bundle.is_dir())

    def test_uninitialized_store_is_rejected_without_initialization_or_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "config"
            store_root = root / "installed"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            store_root.mkdir()
            socket_path = root / "run" / "oracle.sock"
            environment = self._values(bundle, store_root, socket_path)
            with self.assertRaises(Exception):
                start_brain_configuration_host_local_runtime(environment)
            self.assertFalse((store_root / "store.json").exists())
            self.assertFalse(socket_path.exists())

    def test_runtime_start_and_stop_are_idempotent(self) -> None:
        with self._environment() as (_bundle, _store_root, socket_path, environment):
            settings = ConfigurationBootstrapSettings.from_environment(environment)
            runtime = BrainConfigurationHostLocalRuntime(settings)
            self.assertIs(runtime.start(), runtime)
            self.assertIs(runtime.start(), runtime)
            self.assertTrue(socket_path.exists())
            runtime.stop()
            runtime.stop()
            self.assertFalse(socket_path.exists())

    def _environment(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        bundle = root / "config"
        shutil.copytree(EXAMPLE_ROOT, bundle)
        (bundle / "secrets.env.example").unlink()
        store_root = root / "installed"
        GenerationStore(store_root).initialize("example-home")
        socket_path = root / "run" / "oracle.sock"
        environment = self._values(bundle, store_root, socket_path)

        class Environment:
            def __enter__(self):
                return bundle, store_root, socket_path, environment

            def __exit__(self, *_args):
                temporary.cleanup()

        return Environment()

    @staticmethod
    def _values(bundle: Path, store_root: Path, socket_path: Path) -> dict[str, str]:
        return {
            "ORACLE_CONFIG_BUNDLE_ROOT": str(bundle),
            "ORACLE_CONFIG_STORE_ROOT": str(store_root),
            "ORACLE_CONFIG_SOCKET_PATH": str(socket_path),
            "ORACLE_CONFIG_AUTHORING_MODE": "managed_writable",
        }


if __name__ == "__main__":
    unittest.main()
