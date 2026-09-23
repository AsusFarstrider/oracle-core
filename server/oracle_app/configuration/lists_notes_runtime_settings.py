from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    ListsConfiguration,
    MicrosoftTodoListsProvider,
    NextcloudListsProvider,
    NextcloudNotesProvider,
    NotesConfiguration,
)
from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings


@dataclass(frozen=True)
class ProviderObjectIdentity:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    user_ids: tuple[str, ...]
    provider_object_id: str


@dataclass(frozen=True)
class ListsRuntimeSettings:
    config_revision: str
    enabled: bool
    read_enabled: bool
    write_enabled: bool
    confirmation_required: bool
    provider_id: str | None
    provider_type: str | None
    base_url: str | None
    user: str | None
    credential_secret: str | None
    timeout_seconds: int
    fresh_seconds: int
    stale_if_error_seconds: int
    lists: Mapping[str, ProviderObjectIdentity]
    credential: str | None = field(default=None, repr=False)
    default_user_id: str | None = None
    source_user_ids: Mapping[str, str] = field(default_factory=dict)
    oauth_tenant: str | None = None

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> ListsRuntimeSettings:
        role = effective.role("domains/lists.yaml")
        if not isinstance(role, ListsConfiguration):
            raise TypeError("Effective lists role does not use the executable lists schema.")
        provider = _selected_provider(role, (NextcloudListsProvider, MicrosoftTodoListsProvider))
        credential = _credential(effective, role.enabled, provider)
        household = HouseholdRuntimeSettings.from_effective_config(effective)
        return cls(
            config_revision=effective.config_revision,
            enabled=role.enabled,
            read_enabled=role.enabled and role.policy.read_enabled,
            write_enabled=role.enabled and role.policy.write_enabled,
            confirmation_required=role.policy.confirmation_required,
            provider_id=role.provider if role.enabled else None,
            provider_type=None if provider is None else provider.type,
            base_url=None if provider is None else (str(provider.base_url) if isinstance(provider, NextcloudListsProvider) else "https://graph.microsoft.com/v1.0"),
            user=None if provider is None else (provider.user if isinstance(provider, NextcloudListsProvider) else provider.client_id),
            credential_secret=None if provider is None else (provider.credential_secret if isinstance(provider, NextcloudListsProvider) else provider.refresh_token_secret),
            credential=credential,
            timeout_seconds=8 if provider is None else provider.timeout_seconds,
            fresh_seconds=role.policy.fresh_seconds,
            stale_if_error_seconds=role.policy.stale_if_error_seconds,
            lists=MappingProxyType({
                item.id: ProviderObjectIdentity(
                    item.id, item.display_name, tuple(item.aliases), tuple(item.user_ids), item.calendar_uri if isinstance(provider, NextcloudListsProvider) else item.provider_list_id
                )
                for item in (() if provider is None else provider.lists)
            }),
            default_user_id=household.default_user_id,
            source_user_ids=MappingProxyType({source.id: source.associated_user_id for source in household.sources.values() if source.enabled and source.associated_user_id is not None}),
            oauth_tenant=provider.tenant if isinstance(provider, MicrosoftTodoListsProvider) else None,
        )


@dataclass(frozen=True)
class NotesRuntimeSettings:
    config_revision: str
    enabled: bool
    read_enabled: bool
    write_enabled: bool
    confirmation_required: bool
    provider_id: str | None
    provider_type: str | None
    base_url: str | None
    user: str | None
    credential_secret: str | None
    timeout_seconds: int
    fresh_seconds: int
    stale_if_error_seconds: int
    max_content_characters: int
    max_search_results: int
    notes: Mapping[str, ProviderObjectIdentity]
    credential: str | None = field(default=None, repr=False)
    default_user_id: str | None = None
    source_user_ids: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> NotesRuntimeSettings:
        role = effective.role("domains/notes.yaml")
        if not isinstance(role, NotesConfiguration):
            raise TypeError("Effective notes role does not use the executable notes schema.")
        provider = _selected_provider(role, NextcloudNotesProvider)
        credential = _credential(effective, role.enabled, provider)
        household = HouseholdRuntimeSettings.from_effective_config(effective)
        return cls(
            config_revision=effective.config_revision,
            enabled=role.enabled,
            read_enabled=role.enabled and role.policy.read_enabled,
            write_enabled=role.enabled and role.policy.write_enabled,
            confirmation_required=role.policy.confirmation_required,
            provider_id=role.provider if role.enabled else None,
            provider_type=None if provider is None else provider.type,
            base_url=None if provider is None else str(provider.base_url),
            user=None if provider is None else provider.user,
            credential_secret=None if provider is None else provider.credential_secret,
            credential=credential,
            timeout_seconds=8 if provider is None else provider.timeout_seconds,
            fresh_seconds=role.policy.fresh_seconds,
            stale_if_error_seconds=role.policy.stale_if_error_seconds,
            max_content_characters=role.policy.max_content_characters,
            max_search_results=role.policy.max_search_results,
            notes=MappingProxyType({
                item.id: ProviderObjectIdentity(
                    item.id, item.display_name, tuple(item.aliases), tuple(item.user_ids), item.provider_note_id
                )
                for item in (() if provider is None else provider.notes)
            }),
            default_user_id=household.default_user_id,
            source_user_ids=MappingProxyType({source.id: source.associated_user_id for source in household.sources.values() if source.enabled and source.associated_user_id is not None}),
        )


def _selected_provider(role, expected_type):
    if not role.enabled:
        return None
    provider = role.providers[role.provider]
    if not isinstance(provider, expected_type):
        raise TypeError("Configured provider type does not match the domain role.")
    return provider


def _credential(effective: EffectiveConfig, enabled: bool, provider) -> str | None:
    if not enabled or provider is None:
        return None
    credential = effective.secrets.resolve(getattr(provider, "credential_secret", None) or provider.refresh_token_secret)
    if credential is None:
        raise ValueError("Enabled provider lacks its configured credential.")
    return credential
