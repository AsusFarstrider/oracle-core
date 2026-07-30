from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, TypeVar

from .effective import EffectiveConfig
from .models import (
    HouseholdConfiguration,
    HouseholdIdentity,
    HouseholdUiConfiguration,
    ModeConfiguration,
    RoomConfiguration,
    SourceConfiguration,
    UserConfiguration,
)


Identity = TypeVar("Identity", UserConfiguration, RoomConfiguration, ModeConfiguration)


def _normalized_term(value: str) -> str:
    return " ".join(value.casefold().split())


def _identity_map(items: list[Identity]) -> Mapping[str, Identity]:
    return MappingProxyType({item.id: item for item in items})


def _resolution_index(items: list[Identity]) -> Mapping[str, tuple[str, ...]]:
    owners: dict[str, set[str]] = {}
    for item in items:
        if not item.enabled:
            continue
        terms = (item.id, item.display_name, *item.aliases)
        for term in terms:
            normalized = _normalized_term(term)
            if normalized:
                owners.setdefault(normalized, set()).add(item.id)
    return MappingProxyType(
        {term: tuple(sorted(item_ids)) for term, item_ids in owners.items()}
    )


def _resolve(index: Mapping[str, tuple[str, ...]], value: str | None) -> str | None:
    normalized = _normalized_term(value or "")
    if not normalized:
        return None
    matches = index.get(normalized, ())
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class HouseholdRuntimeSettings:
    """Frozen canonical household identity and source-association lookup seam."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    household: HouseholdIdentity
    default_user_id: str | None
    users: Mapping[str, UserConfiguration]
    rooms: Mapping[str, RoomConfiguration]
    sources: Mapping[str, SourceConfiguration]
    modes: Mapping[str, ModeConfiguration]
    ui: HouseholdUiConfiguration
    user_resolution_terms: Mapping[str, tuple[str, ...]]
    room_resolution_terms: Mapping[str, tuple[str, ...]]
    mode_resolution_terms: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> HouseholdRuntimeSettings:
        role = effective.role("household.yaml")
        if not isinstance(role, HouseholdConfiguration):
            raise TypeError("Effective household.yaml role does not use the executable household schema.")
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            household=role.household,
            default_user_id=role.defaults.user_id,
            users=_identity_map(role.users),
            rooms=_identity_map(role.rooms),
            sources=MappingProxyType({item.id: item for item in role.sources}),
            modes=_identity_map(role.modes),
            ui=role.ui,
            user_resolution_terms=_resolution_index(role.users),
            room_resolution_terms=_resolution_index(role.rooms),
            mode_resolution_terms=_resolution_index(role.modes),
        )

    def user(self, user_id: str | None, *, enabled_only: bool = True) -> UserConfiguration | None:
        item = self.users.get(str(user_id or "").strip())
        return item if item is not None and (item.enabled or not enabled_only) else None

    def room(self, room_id: str | None, *, enabled_only: bool = True) -> RoomConfiguration | None:
        item = self.rooms.get(str(room_id or "").strip())
        return item if item is not None and (item.enabled or not enabled_only) else None

    def source(self, source_id: str | None, *, enabled_only: bool = True) -> SourceConfiguration | None:
        item = self.sources.get(str(source_id or "").strip())
        return item if item is not None and (item.enabled or not enabled_only) else None

    def mode(self, mode_id: str | None, *, enabled_only: bool = True) -> ModeConfiguration | None:
        item = self.modes.get(str(mode_id or "").strip())
        return item if item is not None and (item.enabled or not enabled_only) else None

    def default_user(self) -> UserConfiguration | None:
        return self.user(self.default_user_id)

    def resolve_user_id(self, value: str | None) -> str | None:
        return _resolve(self.user_resolution_terms, value)

    def resolve_room_id(self, value: str | None) -> str | None:
        return _resolve(self.room_resolution_terms, value)

    def resolve_mode_id(self, value: str | None) -> str | None:
        return _resolve(self.mode_resolution_terms, value)

    def configured_associated_user_id(self, source_id: str | None) -> str | None:
        source = self.source(source_id)
        if source is None or self.user(source.associated_user_id) is None:
            return None
        return source.associated_user_id

    def configured_associated_room_id(self, source_id: str | None) -> str | None:
        source = self.source(source_id)
        if source is None or not source.fixed or self.room(source.associated_room_id) is None:
            return None
        return source.associated_room_id
