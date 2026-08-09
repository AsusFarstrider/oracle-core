"""Standard Brain process markers and bounded restart recovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import threading
from typing import TYPE_CHECKING, Callable

from .installation import InstallationLayout, InstalledActivation, load_selected_activation

if TYPE_CHECKING:
    from .installation_control import StandardActivationCoordinator


STANDARD_RUNTIME_DIRECTORY = Path("/run/oracle")
STANDARD_PROCESS_MARKER = "running-activation.json"
PROCESS_MARKER_FORMAT = "oracle-running-activation-v1"


class StandardProcessLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class PostExitRecoveryResult:
    outcome: str
    stopped_activation_id: str | None
    selected_activation_id: str


def _coordinator(layout: InstallationLayout) -> StandardActivationCoordinator:
    # Keep configuration imports behind the runtime operation boundary.  The
    # configuration bootstrap imports the restart scheduler from this module,
    # so importing these packages while this module is still initializing
    # creates a circular import in the real standard entrypoint.
    from .configuration.generations import GenerationStore
    from .configuration.service import ConfigurationService
    from .installation_control import StandardActivationCoordinator

    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    store.validate_initialized()
    service = ConfigurationService(store)
    return StandardActivationCoordinator(
        layout,
        service,
        secret_companion_root=layout.secrets,
    )


def _marker_path(runtime_directory: Path) -> Path:
    return Path(runtime_directory) / STANDARD_PROCESS_MARKER


def record_running_activation(
    layout: InstallationLayout = InstallationLayout(),
    *,
    runtime_directory: Path = STANDARD_RUNTIME_DIRECTORY,
    pid: int | None = None,
) -> InstalledActivation:
    """Record the exact complete activation used by this process."""

    from .configuration.generations import _atomic_replace

    selected = load_selected_activation(layout)
    runtime = Path(runtime_directory)
    if runtime.is_symlink() or not runtime.is_dir():
        raise StandardProcessLifecycleError(
            "Standard Oracle runtime directory is absent or unsafe."
        )
    marker = {
        "format": PROCESS_MARKER_FORMAT,
        "activation_id": selected.activation_id,
        "pid": os.getpid() if pid is None else pid,
    }
    if not isinstance(marker["pid"], int) or marker["pid"] <= 0:
        raise StandardProcessLifecycleError("Standard process marker PID is invalid.")
    _atomic_replace(
        _marker_path(runtime),
        (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    return selected


def load_running_activation_id(runtime_directory: Path = STANDARD_RUNTIME_DIRECTORY) -> str | None:
    path = _marker_path(runtime_directory)
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StandardProcessLifecycleError("Standard process marker is invalid.") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "activation_id", "pid"}
        or value.get("format") != PROCESS_MARKER_FORMAT
        or not isinstance(value.get("activation_id"), str)
        or not isinstance(value.get("pid"), int)
        or value["pid"] <= 0
    ):
        raise StandardProcessLifecycleError("Standard process marker is invalid.")
    return value["activation_id"]


def remove_running_activation_marker(
    runtime_directory: Path = STANDARD_RUNTIME_DIRECTORY,
) -> None:
    from .configuration.generations import _fsync_directory

    path = _marker_path(runtime_directory)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def recover_after_process_exit(
    layout: InstallationLayout = InstallationLayout(),
    *,
    runtime_directory: Path = STANDARD_RUNTIME_DIRECTORY,
) -> PostExitRecoveryResult:
    """Implement the unprivileged systemd ExecStopPost recovery decision."""

    coordinator = _coordinator(layout)
    pending = coordinator.pending_candidate_activation_id()
    try:
        stopped = load_running_activation_id(runtime_directory)
    except StandardProcessLifecycleError:
        stopped = None
    if pending is None:
        selected = load_selected_activation(layout)
        remove_running_activation_marker(runtime_directory)
        return PostExitRecoveryResult("no_pending_activation", stopped, selected.activation_id)
    recovered = coordinator.recover_failed_process(stopped)
    remove_running_activation_marker(runtime_directory)
    if recovered is None:
        selected = load_selected_activation(layout)
        return PostExitRecoveryResult(
            "prior_process_exited_candidate_retained",
            stopped,
            selected.activation_id,
        )
    return PostExitRecoveryResult(
        "candidate_failed_previous_restored",
        stopped,
        recovered.activation_id,
    )


def finalize_verified_startup(
    configuration_activation_id: str,
    layout: InstallationLayout = InstallationLayout(),
) -> InstalledActivation | None:
    """Mark a pending candidate known-good after startup verification."""

    coordinator = _coordinator(layout)
    pending = coordinator.pending_candidate_activation_id()
    if pending is None:
        return None
    active = load_selected_activation(layout)
    if active.activation_id != pending:
        raise StandardProcessLifecycleError(
            "Pending candidate is not the complete active activation."
        )
    if active.record.get("configuration_activation_identity") != configuration_activation_id:
        raise StandardProcessLifecycleError(
            "Running configuration does not match the complete active activation."
        )
    return coordinator.finalize_verified()


def schedule_graceful_process_restart(
    *,
    delay_seconds: float = 0.1,
    process_id: int | None = None,
    signal_process: Callable[[int, int], None] = os.kill,
) -> None:
    """Signal this process only after the control response can be flushed."""

    pid = os.getpid() if process_id is None else process_id
    if pid <= 0 or delay_seconds < 0:
        raise StandardProcessLifecycleError("Standard restart request is invalid.")

    timer = threading.Timer(delay_seconds, signal_process, args=(pid, signal.SIGTERM))
    timer.daemon = True
    timer.start()
