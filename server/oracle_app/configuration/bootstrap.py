from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Mapping

from .effective import EffectiveConfig, load_effective_config
from .generations import GenerationStore
from .host_local import HostLocalConfigurationServer
from .service import AuthoringMode, ConfigurationService
from .runtime_cutover import runtime_cutover_required


CONFIGURATION_BOOTSTRAP_ENV_NAMES = frozenset(
    {
        "ORACLE_CONFIG_AUTHORING_MODE",
        "ORACLE_CONFIG_BUNDLE_ROOT",
        "ORACLE_CONFIG_SOCKET_PATH",
        "ORACLE_CONFIG_STORE_ROOT",
    }
)
class ConfigurationBootstrapError(ValueError):
    pass


@dataclass(frozen=True)
class BrainConfigurationStartup:
    mode: str
    service_settings: ConfigurationBootstrapSettings | None
    effective_config: EffectiveConfig | None


def resolve_brain_configuration_startup(
    environment: Mapping[str, str] | None = None,
) -> BrainConfigurationStartup:
    values = os.environ if environment is None else environment
    settings = ConfigurationBootstrapSettings.from_environment(values)
    if settings is None:
        raise ConfigurationBootstrapError(
            "Brain startup requires complete canonical configuration bootstrap settings."
        )

    store = GenerationStore(settings.store_root)
    store.validate_initialized()
    if not runtime_cutover_required(store):
        raise ConfigurationBootstrapError(
            "Canonical runtime has not been armed for this configuration store."
        )
    return BrainConfigurationStartup("canonical", settings, load_effective_config(store))


@dataclass(frozen=True)
class ConfigurationBootstrapSettings:
    bundle_root: Path
    store_root: Path
    socket_path: Path
    authoring_mode: AuthoringMode

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> ConfigurationBootstrapSettings | None:
        values = os.environ if environment is None else environment
        supplied = {name: values.get(name) for name in CONFIGURATION_BOOTSTRAP_ENV_NAMES}
        present = {name for name, value in supplied.items() if value is not None}
        if not present:
            return None
        if present != CONFIGURATION_BOOTSTRAP_ENV_NAMES:
            missing = sorted(CONFIGURATION_BOOTSTRAP_ENV_NAMES - present)
            raise ConfigurationBootstrapError(
                "Canonical configuration bootstrap is incomplete; missing " + ", ".join(missing) + "."
            )
        if any(not str(value).strip() for value in supplied.values()):
            raise ConfigurationBootstrapError("Canonical configuration bootstrap values cannot be empty.")

        mode = str(supplied["ORACLE_CONFIG_AUTHORING_MODE"]).strip()
        if mode not in {"managed_writable", "external_read_only"}:
            raise ConfigurationBootstrapError("Canonical configuration authoring mode is unsupported.")
        try:
            bundle_root = Path(str(supplied["ORACLE_CONFIG_BUNDLE_ROOT"])).resolve(strict=True)
            store_root = Path(str(supplied["ORACLE_CONFIG_STORE_ROOT"])).resolve(strict=True)
        except OSError as exc:
            raise ConfigurationBootstrapError("Canonical configuration bootstrap path does not exist.") from exc
        if not bundle_root.is_dir() or not store_root.is_dir():
            raise ConfigurationBootstrapError("Canonical configuration bundle and store roots must be directories.")
        socket_path = Path(str(supplied["ORACLE_CONFIG_SOCKET_PATH"])).expanduser()
        if not socket_path.is_absolute():
            raise ConfigurationBootstrapError("Canonical configuration socket path must be absolute.")
        if bundle_root == store_root or bundle_root.is_relative_to(store_root) or store_root.is_relative_to(bundle_root):
            raise ConfigurationBootstrapError(
                "Canonical configuration bundle and installed store roots must be disjoint."
            )
        try:
            socket_parent = socket_path.parent.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ConfigurationBootstrapError("Canonical configuration socket parent cannot be resolved.") from exc
        effective_socket_path = socket_parent / socket_path.name
        if effective_socket_path.is_relative_to(bundle_root):
            raise ConfigurationBootstrapError("Canonical configuration socket must be outside the authored bundle root.")
        return cls(
            bundle_root=bundle_root,
            store_root=store_root,
            socket_path=effective_socket_path,
            authoring_mode=mode,  # type: ignore[arg-type]
        )


class BrainConfigurationHostLocalRuntime:
    def __init__(self, settings: ConfigurationBootstrapSettings | None) -> None:
        self.settings = settings
        self._server: HostLocalConfigurationServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.settings is not None

    def start(self) -> BrainConfigurationHostLocalRuntime:
        if self.settings is None or self._server is not None:
            return self
        store = GenerationStore(self.settings.store_root)
        store.validate_initialized()
        service = ConfigurationService(
            store,
            authoring_mode=self.settings.authoring_mode,
            authoring_root=self.settings.bundle_root,
        )
        if self.settings.authoring_mode == "managed_writable":
            service.recover_authoring_transactions(actor="service")
        service.recover_secret_transactions(self.settings.bundle_root, actor="service")
        server = HostLocalConfigurationServer(self.settings.socket_path, service)
        thread = threading.Thread(
            target=server.serve_forever,
            name="oracle-configuration-host-local",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            server.server_close()
            raise
        self._server = server
        self._thread = thread
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()
            if thread is not None:
                thread.join(timeout=5.0)


def start_brain_configuration_host_local_runtime(
    environment: Mapping[str, str] | None = None,
    *,
    startup: BrainConfigurationStartup | None = None,
) -> BrainConfigurationHostLocalRuntime:
    if startup is not None and environment is not None:
        raise ConfigurationBootstrapError(
            "Host-local configuration startup accepts either resolved startup or environment, not both."
        )
    runtime = BrainConfigurationHostLocalRuntime(
        startup.service_settings
        if startup is not None
        else ConfigurationBootstrapSettings.from_environment(environment)
    )
    return runtime.start()
