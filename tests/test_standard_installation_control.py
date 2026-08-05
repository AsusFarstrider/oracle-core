from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    HOST_LOCAL_PROTOCOL_FORMAT,
    ConfigurationService,
    GenerationStore,
    HostLocalDispatcher,
    snapshot_candidate,
)
from oracle_app.installation import (
    ActivationRequest,
    InstallationLayout,
    load_selected_activation,
    publish_activation,
    select_activation,
)
from oracle_app.installation_identity import environment_directory_name
from oracle_app.installation_control import (
    StandardActivationCoordinator,
    standard_online_authorization_audit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class StandardActivationCoordinatorTests(unittest.TestCase):
    def test_online_authorization_audit_is_redacted_and_schema_bounded(self) -> None:
        with self._environment() as (layout, _bundle, _store, _coordinator, _initial):
            audit = standard_online_authorization_audit(layout)
            audit(
                {
                    "operation": "mutate_secret",
                    "result": "accepted",
                    "peer_uid": 1200,
                    "peer_pid": 42,
                    "peer_gid": 1200,
                    "peer_account": "operator",
                    "peer_category": "oracle_operator",
                }
            )

            line = json.loads(
                (layout.control_state / "online-authorization-audit.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(line["format"], "oracle-online-authorization-audit-v1")
            self.assertEqual(line["operation"], "mutate_secret")
            self.assertEqual(line["peer_uid"], 1200)
            self.assertNotIn("value", line)

    def test_host_local_secret_mutation_stages_one_complete_activation(self) -> None:
        with self._environment() as (layout, _bundle, store, coordinator, initial):
            response = HostLocalDispatcher(
                coordinator.service,
                activation_coordinator=coordinator,
            ).dispatch(
                {
                    "format": HOST_LOCAL_PROTOCOL_FORMAT,
                    "operation": "mutate_secret",
                    "secret_operation": "create_secret",
                    "logical_id": "TOKEN",
                    "value": "write-only-value",
                    "expected_secret_generation_id": initial.secrets.generation_id,
                }
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["result"]["restart_required"])
            self.assertEqual(
                response["result"]["staged_complete_activation_id"],
                load_selected_activation(layout).activation_id,
            )
            self.assertEqual(
                store.secret_generation_status(initial.secrets.generation_id)["state"],
                "retirement_pending",
            )
            self.assertNotIn("write-only-value", json.dumps(response))

    def test_verified_secret_activation_finalizes_retirement_and_known_good(self) -> None:
        with self._environment() as (layout, bundle, store, coordinator, initial):
            staged = coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="candidate-value",
                expected_secret_generation_id=initial.secrets.generation_id,
            )

            self.assertEqual(
                store.secret_generation_status(initial.secrets.generation_id)["state"],
                "retirement_pending",
            )
            self.assertEqual(
                load_selected_activation(layout).activation_id,
                staged.candidate_activation.activation_id,
            )
            journal_text = (layout.control_state / "activation-transaction.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("candidate-value", journal_text)

            verified = coordinator.finalize_verified()

            self.assertEqual(verified.activation_id, staged.candidate_activation.activation_id)
            self.assertEqual(
                store.secret_generation_status(initial.secrets.generation_id)["state"],
                "revoked",
            )
            self.assertEqual(
                load_selected_activation(layout, "approved").activation_id,
                verified.activation_id,
            )
            self.assertEqual(
                load_selected_activation(layout, "previous-known-good").activation_id,
                verified.activation_id,
            )
            self.assertFalse((layout.selection / "staged").exists())
            self.assertFalse((layout.control_state / "activation-transaction.json").exists())
            self.assertIn("TOKEN=candidate-value", (bundle / "secrets.env").read_text(encoding="utf-8"))

    def test_failed_secret_activation_restores_prior_complete_combination(self) -> None:
        with self._environment() as (layout, bundle, store, coordinator, initial):
            previous_complete = load_selected_activation(layout)
            staged = coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="must-not-survive",
                expected_secret_generation_id=initial.secrets.generation_id,
            )

            recovered = coordinator.recover_failed()

            self.assertEqual(recovered.activation_id, previous_complete.activation_id)
            self.assertEqual(load_selected_activation(layout).activation_id, previous_complete.activation_id)
            self.assertEqual(store.load_selected().activation.generation_id, initial.activation.generation_id)
            self.assertEqual(
                store.secret_generation_status(initial.secrets.generation_id)["state"],
                "available",
            )
            self.assertEqual(
                store.secret_generation_status(staged.mutation.secret_generation_id)["state"],
                "revoked",
            )
            self.assertNotIn("TOKEN", (bundle / "secrets.env").read_text(encoding="utf-8"))
            self.assertFalse((layout.selection / "staged").exists())
            results = list(layout.control_state.glob("complete_activation_tx_*.json"))
            self.assertEqual(len(results), 1)
            self.assertEqual(json.loads(results[0].read_text())["outcome"], "recovered_previous")

    def test_recovery_finishes_irreversible_finalization_after_verification_marker(self) -> None:
        with self._environment() as (layout, _bundle, store, coordinator, initial):
            staged = coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="candidate-value",
                expected_secret_generation_id=initial.secrets.generation_id,
            )
            journal = coordinator._load_journal()  # noqa: SLF001 - crash-point simulation
            journal["state"] = "verification_passed"
            coordinator._write_journal(journal)  # noqa: SLF001 - crash-point simulation
            store.finalize_secret_retirement(
                initial.secrets.generation_id,
                replaced_by=staged.mutation.secret_generation_id,
            )

            recovered = coordinator.recover()

            self.assertEqual(recovered.activation_id, staged.candidate_activation.activation_id)
            self.assertEqual(
                load_selected_activation(layout, "approved").activation_id,
                staged.candidate_activation.activation_id,
            )
            self.assertFalse((layout.control_state / "activation-transaction.json").exists())

    def _environment(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        layout = InstallationLayout(root / "oracle")
        for directory in layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        bundle = layout.secrets
        shutil.copytree(EXAMPLE_ROOT, root / "candidate")
        candidate = root / "candidate"
        store = GenerationStore(layout.configuration, secret_root=layout.secrets)
        store.initialize("example-home")
        service = ConfigurationService(store)
        activated = service.activate_candidate(
            candidate,
            expected_authored_revision=snapshot_candidate(candidate).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
        )
        request = ActivationRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-tree-" + "2" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
            configuration_activation_identity=activated.selected.activation.generation_id,
            service_definition_identity="systemd-unit-" + "5" * 64,
        )
        for path in (
            layout.revisions / request.application_revision_identity,
            layout.environments / environment_directory_name(request.python_environment_identity),
            layout.deployments / request.household_deployment_revision,
        ):
            path.mkdir()
        complete = publish_activation(layout, request)
        select_activation(layout, "active", complete)
        select_activation(layout, "approved", complete)
        coordinator = StandardActivationCoordinator(
            layout,
            service,
            secret_companion_root=bundle,
        )

        class Environment:
            def __enter__(self_nonlocal):
                return layout, bundle, store, coordinator, activated.selected

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return Environment()


if __name__ == "__main__":
    unittest.main()
