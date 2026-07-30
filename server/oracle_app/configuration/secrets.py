from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from .loader import LoadedBundle
from .network_adapter_selection import active_network_adapter_ids
from .validation import ConfigurationFinding


_SECRET_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretCompanionError(ValueError):
    def __init__(self, code: str, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.line = line


class SecretSnapshot:
    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = MappingProxyType(dict(values or {}))

    def __repr__(self) -> str:
        return f"SecretSnapshot(present_ids={len(self._values)})"

    @property
    def present_ids(self) -> frozenset[str]:
        return frozenset(self._values)

    def resolve(self, logical_id: str) -> str | None:
        return self._values.get(logical_id)

    def _with_value(self, logical_id: str, value: str) -> SecretSnapshot:
        values = dict(self._values)
        values[logical_id] = value
        return SecretSnapshot(values)

    def _without_value(self, logical_id: str) -> SecretSnapshot:
        values = dict(self._values)
        values.pop(logical_id, None)
        return SecretSnapshot(values)

    def _matches(self, other: SecretSnapshot) -> bool:
        return self._values == other._values

    def _companion_bytes(self) -> bytes:
        return "".join(f"{logical_id}={self._values[logical_id]}\n" for logical_id in sorted(self._values)).encode("utf-8")


@dataclass(frozen=True, order=True)
class SecretReferenceUse:
    logical_id: str
    file_role: str
    path: str
    required: bool


def parse_secret_companion(data: bytes | str) -> SecretSnapshot:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretCompanionError("config.secret.utf8", "Secret companion must be valid UTF-8.") from exc
    elif isinstance(data, str):
        text = data
    else:
        raise TypeError("Secret companion must be bytes or text.")
    if text.startswith("\ufeff"):
        raise SecretCompanionError("config.secret.bom", "A UTF-8 byte-order mark is not allowed.", line=1)

    values: dict[str, str] = {}
    seen: set[str] = set()
    for line_number, physical_line in enumerate(text.splitlines(), start=1):
        if not physical_line.strip() or physical_line.lstrip().startswith("#"):
            continue
        if "=" not in physical_line:
            raise SecretCompanionError(
                "config.secret.line",
                "Secret entries must use KEY=value on one physical line.",
                line=line_number,
            )
        raw_key, value = physical_line.split("=", 1)
        key = raw_key.strip()
        if _SECRET_ID_PATTERN.fullmatch(key) is None:
            raise SecretCompanionError(
                "config.secret.key",
                "Secret keys must be uppercase logical IDs.",
                line=line_number,
            )
        if key in seen:
            raise SecretCompanionError(
                "config.secret.duplicate",
                f"Duplicate logical secret ID {key!r}.",
                line=line_number,
            )
        seen.add(key)
        if value != "":
            values[key] = value
    return SecretSnapshot(values)


def load_secret_companion(root: Path) -> SecretSnapshot:
    resolved_root = Path(root).resolve(strict=True)
    companion = resolved_root / "secrets.env"
    if not companion.exists() and not companion.is_symlink():
        return SecretSnapshot()
    try:
        target = companion.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SecretCompanionError("config.secret.target", f"Secret companion cannot be resolved: {exc}") from exc
    if not target.is_relative_to(resolved_root):
        raise SecretCompanionError("config.secret.path_escape", "Secret companion escapes the resolved bundle root.")
    if not target.is_file():
        raise SecretCompanionError("config.secret.target", "Secret companion target must be a regular file.")
    return parse_secret_companion(target.read_bytes())


def collect_secret_references(bundle: LoadedBundle) -> tuple[SecretReferenceUse, ...]:
    household = bundle.household
    access = bundle.access
    satellites = bundle.satellites
    uses: list[SecretReferenceUse] = []

    def add(logical_id: str | None, role: str, path: str, required: bool) -> None:
        if logical_id is not None:
            uses.append(SecretReferenceUse(logical_id, role, path, required))

    for user_index, user in enumerate(household.users):
        capability = user.capabilities.audiobooks
        if capability is not None and capability.credential_secret is not None:
            uses.append(
                SecretReferenceUse(
                    capability.credential_secret,
                    "household.yaml",
                    f"users[{user_index}].capabilities.audiobooks.credential_secret",
                    user.enabled and capability.enabled,
                )
            )

    sources_by_id = {source.id: source for source in household.sources}
    bindings = access.source_authentication.credential_bindings if access.source_authentication else []
    for binding_index, binding in enumerate(bindings):
        source = sources_by_id.get(binding.source_id)
        uses.append(
            SecretReferenceUse(
                binding.credential_secret,
                "access.yaml",
                f"source_authentication.credential_bindings[{binding_index}].credential_secret",
                bool(source and source.enabled),
            )
        )

    for satellite_index, satellite in enumerate(satellites.satellites):
        for field_name in ("brain_client", "control_service", "enrollment"):
            credential = getattr(satellite, field_name)
            if credential is not None:
                uses.append(
                    SecretReferenceUse(
                        credential.credential_secret,
                        "satellites.yaml",
                        f"satellites[{satellite_index}].{field_name}.credential_secret",
                        satellite.enabled,
                    )
                )

    information = bundle.roles.get("domains/information.yaml")
    if information is not None:
        suggestions = information.suggestions  # type: ignore[attr-defined]
        for provider_id, provider in suggestions.providers.items():
            selected = suggestions.enabled and suggestions.provider == provider_id
            add(
                getattr(provider, "password_secret", None),
                "domains/information.yaml",
                f"suggestions.providers.{provider_id}.password_secret",
                selected,
            )
            add(
                getattr(provider, "base_url_secret", None),
                "domains/information.yaml",
                f"suggestions.providers.{provider_id}.base_url_secret",
                selected,
            )

    music = bundle.roles.get("domains/music.yaml")
    if music is not None:
        for provider_id, provider in music.providers.items():  # type: ignore[attr-defined]
            add(
                provider.credential_secret,
                "domains/music.yaml",
                f"providers.{provider_id}.credential_secret",
                music.enabled and music.provider == provider_id,  # type: ignore[attr-defined]
            )

    weather = bundle.roles.get("domains/weather.yaml")
    if weather is not None:
        for provider_id, provider in weather.providers.items():  # type: ignore[attr-defined]
            fallback = getattr(provider, "history_ssh_fallback", None)
            add(
                None if fallback is None else fallback.password_secret,
                "domains/weather.yaml",
                f"providers.{provider_id}.history_ssh_fallback.password_secret",
                weather.enabled and weather.history.enabled and weather.history.provider == provider_id,  # type: ignore[attr-defined]
            )

    calendar = bundle.roles.get("domains/calendar.yaml")
    if calendar is not None:
        for provider_id, provider in calendar.providers.items():  # type: ignore[attr-defined]
            selected_for_read = calendar.enabled and calendar.policy.read_enabled and calendar.provider == provider_id  # type: ignore[attr-defined]
            for feed_index, feed in enumerate(provider.feeds):
                add(
                    feed.ics_url_secret,
                    "domains/calendar.yaml",
                    f"providers.{provider_id}.feeds[{feed_index}].ics_url_secret",
                    selected_for_read,
                )
            add(
                provider.write_credential_secret,
                "domains/calendar.yaml",
                f"providers.{provider_id}.write_credential_secret",
                calendar.enabled and calendar.policy.write_enabled and calendar.provider == provider_id,  # type: ignore[attr-defined]
            )

    home_assistant = bundle.roles.get("domains/home-assistant.yaml")
    if home_assistant is not None:
        enabled_automation = any(automation.enabled for automation in home_assistant.automations)  # type: ignore[attr-defined]
        for provider_id, provider in home_assistant.providers.items():  # type: ignore[attr-defined]
            selected = home_assistant.enabled and home_assistant.provider == provider_id  # type: ignore[attr-defined]
            add(provider.credential_secret, "domains/home-assistant.yaml", f"providers.{provider_id}.credential_secret", selected)
            add(
                provider.event_ingress_secret,
                "domains/home-assistant.yaml",
                f"providers.{provider_id}.event_ingress_secret",
                selected and enabled_automation,
            )

    notifications = bundle.roles.get("domains/notifications.yaml")
    if notifications is not None:
        active_group_ids = {
            group_id
            for notification in notifications.types  # type: ignore[attr-defined]
            if notification.enabled
            and notification.external_delivery is not None
            and notification.external_delivery.enabled
            for group_id in notification.external_delivery.recipient_groups
        }
        active_provider_ids = {
            group.provider
            for group in notifications.recipient_groups  # type: ignore[attr-defined]
            if group.enabled and group.id in active_group_ids
        }
        for provider_id, provider in notifications.providers.items():  # type: ignore[attr-defined]
            add(
                provider.base_url_secret,
                "domains/notifications.yaml",
                f"providers.{provider_id}.base_url_secret",
                notifications.enabled and provider_id in active_provider_ids,  # type: ignore[attr-defined]
            )

    adapters = bundle.roles.get("domains/network/adapters.yaml")
    inventory = bundle.roles.get("domains/network/inventory.yaml")
    policy = bundle.roles.get("domains/network/policy.yaml")
    if adapters is not None:
        active_adapter_ids = active_network_adapter_ids(inventory, policy, adapters)  # type: ignore[arg-type]
        for adapter_id, adapter in adapters.providers.items():  # type: ignore[attr-defined]
            required = adapter_id in active_adapter_ids
            add(getattr(adapter, "credential_secret", None), "domains/network/adapters.yaml", f"providers.{adapter_id}.credential_secret", required)
            add(getattr(adapter, "password_secret", None), "domains/network/adapters.yaml", f"providers.{adapter_id}.password_secret", required)
    return tuple(sorted(uses))


def validate_secret_snapshot(
    bundle: LoadedBundle,
    snapshot: SecretSnapshot,
) -> tuple[tuple[ConfigurationFinding, ...], tuple[ConfigurationFinding, ...]]:
    uses = collect_secret_references(bundle)
    blockers: list[ConfigurationFinding] = []
    referenced_ids = {use.logical_id for use in uses}
    for use in uses:
        if use.required and use.logical_id not in snapshot.present_ids:
            blockers.append(
                ConfigurationFinding(
                    code="config.secret.required_missing",
                    file_role=use.file_role,
                    path=use.path,
                    message=f"Required logical secret {use.logical_id!r} is absent.",
                    category="activation",
                    owner="secrets",
                )
            )
    warnings = [
        ConfigurationFinding(
            code="config.secret.unreferenced",
            file_role="secrets.env",
            path=logical_id,
            message=f"Logical secret {logical_id!r} is not referenced by configuration.",
            severity="warning",
            blocks_activation=False,
            category="validation",
            owner="secrets",
        )
        for logical_id in sorted(snapshot.present_ids - referenced_ids)
    ]
    return tuple(sorted(blockers)), tuple(warnings)
