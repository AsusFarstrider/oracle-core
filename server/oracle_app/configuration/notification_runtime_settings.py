from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    AppriseNotificationProvider,
    NotificationRecipientGroup,
    NotificationType,
    NotificationsConfiguration,
)
from .effective import EffectiveConfig


@dataclass(frozen=True)
class NotificationProviderRuntimeSettings:
    provider_id: str
    type: str
    credential_free_base_url: str | None
    base_url_secret: str | None
    timeout_seconds: int
    resolved_base_url: str = field(repr=False)


@dataclass(frozen=True)
class NotificationRecipientGroupRuntimeSettings:
    definition: NotificationRecipientGroup
    provider: NotificationProviderRuntimeSettings


@dataclass(frozen=True)
class NotificationTypeRuntimeSettings:
    definition: NotificationType
    external_recipient_groups: Mapping[str, NotificationRecipientGroupRuntimeSettings]

    @property
    def source_audience_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.definition.audience if item.type == "source")

@dataclass(frozen=True)
class NotificationRuntimeSettings:
    """Frozen Brain execution settings for the optional notifications role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    types: Mapping[str, NotificationTypeRuntimeSettings]
    recipient_groups: Mapping[str, NotificationRecipientGroupRuntimeSettings]
    providers: Mapping[str, NotificationProviderRuntimeSettings]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> NotificationRuntimeSettings:
        role = effective.role("domains/notifications.yaml")
        if not isinstance(role, NotificationsConfiguration):
            raise TypeError(
                "Effective notifications role does not use the executable notifications schema."
            )

        provider_settings: dict[str, NotificationProviderRuntimeSettings] = {}
        group_settings: dict[str, NotificationRecipientGroupRuntimeSettings] = {}
        type_settings: dict[str, NotificationTypeRuntimeSettings] = {}
        if role.enabled:
            groups_by_id = {group.id: group for group in role.recipient_groups}
            for notification in role.types:
                if not notification.enabled:
                    continue
                bound_groups: dict[str, NotificationRecipientGroupRuntimeSettings] = {}
                external = notification.external_delivery
                if external is not None and external.enabled:
                    for group_id in external.recipient_groups:
                        group_runtime = group_settings.get(group_id)
                        if group_runtime is None:
                            group = groups_by_id[group_id]
                            if not group.enabled:
                                raise ValueError(
                                    "Enabled canonical external notification references a disabled group."
                                )
                            provider_runtime = provider_settings.get(group.provider)
                            if provider_runtime is None:
                                provider_runtime = _provider_settings(
                                    effective,
                                    group.provider,
                                    role.providers[group.provider],
                                )
                                provider_settings[group.provider] = provider_runtime
                            group_runtime = NotificationRecipientGroupRuntimeSettings(
                                definition=group,
                                provider=provider_runtime,
                            )
                            group_settings[group_id] = group_runtime
                        bound_groups[group_id] = group_runtime
                type_settings[notification.id] = NotificationTypeRuntimeSettings(
                    definition=notification,
                    external_recipient_groups=MappingProxyType(bound_groups),
                )

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            types=MappingProxyType(type_settings),
            recipient_groups=MappingProxyType(group_settings),
            providers=MappingProxyType(provider_settings),
        )

    def notification_type(self, notification_id: str | None) -> NotificationTypeRuntimeSettings | None:
        return self.types.get(str(notification_id or "").strip())


def _provider_settings(
    effective: EffectiveConfig,
    provider_id: str,
    provider: AppriseNotificationProvider,
) -> NotificationProviderRuntimeSettings:
    resolved_base_url = None if provider.base_url is None else str(provider.base_url)
    if provider.base_url_secret is not None:
        resolved_base_url = effective.secrets.resolve(provider.base_url_secret)
    if resolved_base_url is None:
        raise ValueError(
            f"Enabled canonical notification provider {provider_id!r} lacks its base URL value."
        )
    return NotificationProviderRuntimeSettings(
        provider_id=provider_id,
        type=provider.type,
        credential_free_base_url=None if provider.base_url is None else str(provider.base_url),
        base_url_secret=provider.base_url_secret,
        timeout_seconds=provider.timeout_seconds,
        resolved_base_url=resolved_base_url,
    )
