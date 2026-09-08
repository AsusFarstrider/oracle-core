from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Mapping

from .domain_models import CalendarConfiguration, NextcloudCalendarProvider
from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings


@dataclass(frozen=True)
class CalendarFeedRuntimeSettings:
    id: str
    kind: str
    label: str
    user_ids: tuple[str, ...]
    credential_free_url: str | None
    url_secret: str | None
    read_user: str | None
    read_credential_secret: str | None
    resolved_url: str = field(repr=False)
    read_credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CalendarPersonRuntimeSettings:
    id: str
    display_name: str
    aliases: tuple[str, ...]


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
    default_person_id: str | None
    people: Mapping[str, CalendarPersonRuntimeSettings]
    source_person_ids: Mapping[str, str]
    read: CalendarReadRuntimeSettings
    write: CalendarWriteRuntimeSettings

    def person_id_for_query(self, text: str, *, source_id: str | None = None) -> str | None:
        normalized = " ".join(str(text or "").casefold().split())
        matches: set[str] = set()
        for person in self.people.values():
            for term in (person.id, person.display_name, *person.aliases):
                if re.search(rf"\b{re.escape(' '.join(term.casefold().split()))}(?:'s)?\b", normalized):
                    matches.add(person.id)
        if len(matches) == 1:
            return next(iter(matches))
        personal_markers = (" my ", " am i ", " do i ", " what am i ", " i need ")
        padded = f" {normalized} "
        if any(marker in padded for marker in personal_markers):
            return self.source_person_ids.get(str(source_id or "")) or self.default_person_id
        return None

    def calendar_id_for_query(self, text: str) -> str | None:
        normalized = " ".join(str(text or "").casefold().split())
        matches = {
            feed.id
            for feed in self.read.feeds_for_kind("events")
            if feed.label.strip()
            and re.search(
                rf"\b(?:{re.escape(' '.join(feed.label.casefold().split()))}\s+calendar|calendar\s+{re.escape(' '.join(feed.label.casefold().split()))})\b",
                normalized,
            )
        }
        return next(iter(matches)) if len(matches) == 1 else None

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
            default_person_id=household.default_user_id,
            people=MappingProxyType({
                user.id: CalendarPersonRuntimeSettings(
                    id=user.id,
                    display_name=user.display_name,
                    aliases=tuple(user.aliases),
                )
                for user in household.users.values()
                if user.enabled
            }),
            source_person_ids=MappingProxyType({
                source.id: source.associated_user_id
                for source in household.sources.values()
                if source.enabled and source.associated_user_id is not None
            }),
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
            read_credential = (
                effective.secrets.resolve(feed.read_credential_secret)
                if feed.read_credential_secret is not None
                else None
            )
            if feed.read_credential_secret is not None and read_credential is None:
                raise ValueError(f"Enabled canonical calendar feed {feed.id!r} lacks its read credential.")
            feeds[feed.id] = CalendarFeedRuntimeSettings(
                id=feed.id,
                kind=feed.kind,
                label=feed.label or feed.id.replace("_", " ").title(),
                user_ids=tuple(feed.user_ids),
                credential_free_url=None if feed.ics_url is None else str(feed.ics_url),
                url_secret=feed.ics_url_secret,
                read_user=feed.read_user,
                read_credential_secret=feed.read_credential_secret,
                resolved_url=resolved_url,
                read_credential=read_credential,
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
