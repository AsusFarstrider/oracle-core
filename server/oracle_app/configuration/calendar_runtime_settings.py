from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import CalendarConfiguration, NextcloudCalendarProvider
from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings


@dataclass(frozen=True)
class CalendarFeedRuntimeSettings:
    id: str
    kind: str
    credential_free_url: str | None
    url_secret: str | None
    resolved_url: str = field(repr=False)


@dataclass(frozen=True)
class CalendarReadRuntimeSettings:
    enabled: bool
    feeds: Mapping[str, CalendarFeedRuntimeSettings]
    fresh_seconds: int
    stale_if_error_seconds: int

    def feeds_for_kind(self, kind: str) -> tuple[CalendarFeedRuntimeSettings, ...]:
        return tuple(feed for feed in self.feeds.values() if feed.kind == kind)


@dataclass(frozen=True)
class CalendarWriteRuntimeSettings:
    enabled: bool
    confirmation_required: bool
    base_url: str | None
    user: str | None
    calendar_uri: str | None
    credential_secret: str | None
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CalendarRuntimeSettings:
    """Frozen Brain execution settings for the optional calendar domain role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    provider_id: str | None
    provider_type: str | None
    timezone: str
    timeout_seconds: int | None
    read: CalendarReadRuntimeSettings
    write: CalendarWriteRuntimeSettings

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> CalendarRuntimeSettings:
        role = effective.role("domains/calendar.yaml")
        if not isinstance(role, CalendarConfiguration):
            raise TypeError("Effective calendar role does not use the executable calendar schema.")

        household = HouseholdRuntimeSettings.from_effective_config(effective)
        provider_id = None
        provider = None
        if role.enabled:
            provider_id = role.provider
            if provider_id is None:
                raise ValueError("Enabled canonical calendar has no selected provider.")
            provider = role.providers[provider_id]
            if not isinstance(provider, NextcloudCalendarProvider):
                raise TypeError("Canonical calendar does not select a Nextcloud provider.")

        read = _read_settings(effective, role, provider)
        write = _write_settings(effective, role, provider)
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            provider_id=provider_id,
            provider_type=None if provider is None else provider.type,
            timezone=household.household.timezone,
            timeout_seconds=None if provider is None else provider.timeout_seconds,
            read=read,
            write=write,
        )


def _read_settings(
    effective: EffectiveConfig,
    role: CalendarConfiguration,
    provider: NextcloudCalendarProvider | None,
) -> CalendarReadRuntimeSettings:
    enabled = role.enabled and role.policy.read_enabled
    feeds: dict[str, CalendarFeedRuntimeSettings] = {}
    if enabled:
        if provider is None:
            raise ValueError("Enabled canonical calendar read has no selected provider.")
        for feed in provider.feeds:
            resolved_url = None if feed.ics_url is None else str(feed.ics_url)
            if feed.ics_url_secret is not None:
                resolved_url = effective.secrets.resolve(feed.ics_url_secret)
            if resolved_url is None:
                raise ValueError(f"Enabled canonical calendar feed {feed.id!r} lacks its URL value.")
            feeds[feed.id] = CalendarFeedRuntimeSettings(
                id=feed.id,
                kind=feed.kind,
                credential_free_url=None if feed.ics_url is None else str(feed.ics_url),
                url_secret=feed.ics_url_secret,
                resolved_url=resolved_url,
            )
    return CalendarReadRuntimeSettings(
        enabled=enabled,
        feeds=MappingProxyType(feeds),
        fresh_seconds=role.policy.fresh_seconds,
        stale_if_error_seconds=role.policy.stale_if_error_seconds,
    )


def _write_settings(
    effective: EffectiveConfig,
    role: CalendarConfiguration,
    provider: NextcloudCalendarProvider | None,
) -> CalendarWriteRuntimeSettings:
    enabled = role.enabled and role.policy.write_enabled
    if not enabled:
        return CalendarWriteRuntimeSettings(
            enabled=False,
            confirmation_required=role.policy.confirmation_required,
            base_url=None,
            user=None,
            calendar_uri=None,
            credential_secret=None,
        )
    if (
        provider is None
        or provider.write_base_url is None
        or provider.write_user is None
        or provider.write_calendar_uri is None
        or provider.write_credential_secret is None
    ):
        raise ValueError("Enabled canonical calendar write lacks its complete provider tuple.")
    credential = effective.secrets.resolve(provider.write_credential_secret)
    if credential is None:
        raise ValueError("Enabled canonical calendar write lacks its credential.")
    return CalendarWriteRuntimeSettings(
        enabled=True,
        confirmation_required=role.policy.confirmation_required,
        base_url=str(provider.write_base_url),
        user=provider.write_user,
        calendar_uri=provider.write_calendar_uri,
        credential_secret=provider.write_credential_secret,
        credential=credential,
    )
