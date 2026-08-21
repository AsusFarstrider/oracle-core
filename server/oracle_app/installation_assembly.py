"""Offline initial configuration and complete-activation assembly."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .configuration import (
    ConfigurationService,
    GenerationStore,
    arm_runtime_cutover,
    inspect_candidate,
    snapshot_candidate,
)
from .configuration.secrets import SecretSnapshot
from .installation import (
    ActivationRequest,
    InstallationLayout,
    InstalledActivation,
    load_selected_activation,
    publish_activation,
    select_activation,
)
from .installation_identity import environment_directory_name


class InitialAssemblyError(RuntimeError):
    """The staged components cannot form one initial standard activation."""


@dataclass(frozen=True)
class InitialAssemblyRequest:
    core_commit: str
    core_git_tree: str
    application_revision_identity: str
    python_environment_identity: str
    household_deployment_revision: str
    configuration_root: str = "configuration"
    service_definition_path: str = "scripts/oracle-brain-standard.service"
    initial_secret_snapshot: SecretSnapshot | None = None
    safety_acknowledgements: frozenset[str] = frozenset()


def service_definition_identity(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise InitialAssemblyError("The staged standard service definition is absent or unsafe.")
    return "systemd-unit-" + hashlib.sha256(path.read_bytes()).hexdigest()


def assemble_initial_activation(
    layout: InstallationLayout,
    request: InitialAssemblyRequest,
) -> InstalledActivation:
    """Create the first canonical generations and leave one complete activation staged."""

    for selection in ("active", "approved", "previous-known-good"):
        path = layout.selection / selection
        if path.exists() or path.is_symlink():
            raise InitialAssemblyError("Initial assembly cannot replace an established installation selection.")
    staged = layout.selection / "staged"
    if staged.exists() or staged.is_symlink():
        raise InitialAssemblyError("Initial assembly requires an empty staged selection.")

    application = layout.revisions / request.application_revision_identity
    deployment = layout.deployments / request.household_deployment_revision
    environment = layout.environments / environment_directory_name(request.python_environment_identity)
    for label, path in (
        ("application revision", application),
        ("household deployment", deployment),
        ("Python environment", environment),
    ):
        if path.is_symlink() or not path.is_dir() or path.parent not in {
            layout.revisions,
            layout.deployments,
            layout.environments,
        }:
            raise InitialAssemblyError(f"The staged {label} is absent or unsafe.")

    bundle = deployment / request.configuration_root
    if bundle.is_symlink() or not bundle.is_dir() or not bundle.resolve().is_relative_to(deployment.resolve()):
        raise InitialAssemblyError("The staged canonical configuration root is absent or unsafe.")
    inspection = (
        inspect_candidate(bundle)
        if request.initial_secret_snapshot is None
        else inspect_candidate(bundle, secret_snapshot=request.initial_secret_snapshot)
    )
    if not inspection.report.activation_eligible or inspection.bundle is None:
        raise InitialAssemblyError("The staged canonical configuration is not activation eligible.")
    bundle_id = inspection.bundle.roles["bundle.yaml"].bundle_id

    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    store.initialize(bundle_id)
    service = ConfigurationService(store)
    activated = service.activate_candidate(
        bundle,
        expected_authored_revision=snapshot_candidate(bundle).authored_revision,
        expected_secret_generation_id=None,
        actor="host_local_cli",
        acknowledgements=request.safety_acknowledgements,
        initial_secret_snapshot=request.initial_secret_snapshot,
    )
    selected = store.load_selected()
    arm_runtime_cutover(store, selected, actor="host_local_cli", audit_event_id=activated.audit_event_id)

    complete = publish_activation(
        layout,
        ActivationRequest(
            core_commit=request.core_commit,
            core_git_tree=request.core_git_tree,
            application_revision_identity=request.application_revision_identity,
            python_environment_identity=request.python_environment_identity,
            household_deployment_revision=request.household_deployment_revision,
            configuration_activation_identity=selected.activation.generation_id,
            service_definition_identity=service_definition_identity(
                application / request.service_definition_path
            ),
        ),
    )
    select_activation(layout, "staged", complete)
    return complete


def assemble_update_activation(
    layout: InstallationLayout,
    request: InitialAssemblyRequest,
) -> InstalledActivation:
    """Stage one complete application update against the selected config.

    Stage 4 deliberately keeps application/deployment updates separate from
    configuration authoring.  A repinned household bundle may participate only
    when canonical validation proves it is an exact effective no-op relative
    to the currently selected configuration and secret generations.
    """

    active = load_selected_activation(layout)
    known_good = load_selected_activation(layout, "previous-known-good")
    if active.activation_id != known_good.activation_id:
        raise InitialAssemblyError("Update assembly requires the active activation to be known-good.")
    staged = layout.selection / "staged"
    if staged.exists() or staged.is_symlink():
        raise InitialAssemblyError("Update assembly requires no existing staged activation.")

    application = layout.revisions / request.application_revision_identity
    deployment = layout.deployments / request.household_deployment_revision
    environment = layout.environments / environment_directory_name(request.python_environment_identity)
    for label, path in (
        ("application revision", application),
        ("household deployment", deployment),
        ("Python environment", environment),
    ):
        if path.is_symlink() or not path.is_dir() or path.parent not in {
            layout.revisions,
            layout.deployments,
            layout.environments,
        }:
            raise InitialAssemblyError(f"The staged {label} is absent or unsafe.")

    bundle = deployment / request.configuration_root
    if bundle.is_symlink() or not bundle.is_dir() or not bundle.resolve().is_relative_to(deployment.resolve()):
        raise InitialAssemblyError("The staged canonical configuration root is absent or unsafe.")
    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    store.validate_initialized()
    selected = store.load_selected()
    if active.record.get("configuration_activation_identity") != selected.activation.generation_id:
        raise InitialAssemblyError("Active installation and canonical configuration selections disagree.")
    inspection = inspect_candidate(bundle, secret_snapshot=selected.secrets.snapshot)
    if not inspection.report.activation_eligible or inspection.normalized is None:
        raise InitialAssemblyError("The staged canonical configuration is not activation eligible.")
    if inspection.normalized.config_revision != selected.config.config_revision:
        raise InitialAssemblyError(
            "Application update cannot implicitly change canonical configuration; use its transaction lifecycle first."
        )

    complete = publish_activation(
        layout,
        ActivationRequest(
            core_commit=request.core_commit,
            core_git_tree=request.core_git_tree,
            application_revision_identity=request.application_revision_identity,
            python_environment_identity=request.python_environment_identity,
            household_deployment_revision=request.household_deployment_revision,
            configuration_activation_identity=selected.activation.generation_id,
            service_definition_identity=service_definition_identity(
                application / request.service_definition_path
            ),
            persistent_state_checkpoint=active.record.get("persistent_state_checkpoint"),
        ),
    )
    if complete.activation_id == active.activation_id:
        raise InitialAssemblyError("Update activation must differ from the active complete activation.")
    select_activation(layout, "staged", complete)
    return complete
