from __future__ import annotations

from types import SimpleNamespace

import pytest

from oracle_app.alert_targeting import resolve_alert_targets


class _Household:
    household = SimpleNamespace(id="home")

    def __init__(self) -> None:
        self.config_revision = "revision-1"
        self.rooms = {"kitchen": object(), "office": object()}
        self.users = {"phil": object(), "molly": object()}
        self.sources = {
            "phil-a": ("kitchen", "phil"),
            "phil-b": ("office", "phil"),
            "molly": ("kitchen", "molly"),
            "common": ("kitchen", None),
            "disabled": ("office", "phil"),
        }

    def room(self, room_id):
        return self.rooms.get(room_id)

    def user(self, user_id):
        return self.users.get(user_id)

    def source(self, source_id):
        return SimpleNamespace(id=source_id) if source_id in self.sources else None

    def configured_associated_room_id(self, source_id):
        return self.sources[source_id][0]

    def configured_associated_user_id(self, source_id):
        return self.sources[source_id][1]


class _Fleet:
    enabled_satellite_ids_by_source = {
        "phil-a": "one", "phil-b": "two", "molly": "three", "common": "four",
    }

    def satellite_for_source(self, source_id):
        if source_id not in self.enabled_satellite_ids_by_source:
            return None
        return SimpleNamespace(alert_capable=True)


def _sources(result):
    return [item.source_id for item in result.destinations]


def test_local_room_and_household_targets_use_enabled_alert_capable_satellites() -> None:
    household, fleet = _Household(), _Fleet()
    assert _sources(resolve_alert_targets(
        household=household, satellites=fleet, scope="local", requesting_source_id="phil-a"
    )) == ["phil-a"]
    assert _sources(resolve_alert_targets(
        household=household, satellites=fleet, scope="room", requesting_source_id="phil-a", target_id="kitchen"
    )) == ["common", "molly", "phil-a"]
    assert _sources(resolve_alert_targets(
        household=household, satellites=fleet, scope="household", requesting_source_id="phil-a"
    )) == ["common", "molly", "phil-a", "phil-b"]


def test_recipient_targets_are_independent_from_optional_common_copy() -> None:
    result = resolve_alert_targets(
        household=_Household(), satellites=_Fleet(), scope="recipient",
        requesting_source_id="common", recipient_user_ids=("phil",), include_common_copy=True,
    )
    assert [(item.source_id, item.recipient_user_id, item.role) for item in result.destinations] == [
        ("phil-a", "phil", "destination"),
        ("phil-b", "phil", "destination"),
        ("common", None, "common"),
    ]


def test_target_resolution_fails_closed_for_unknown_or_ineligible_targets() -> None:
    with pytest.raises(ValueError, match="not an enabled"):
        resolve_alert_targets(
            household=_Household(), satellites=_Fleet(), scope="local", requesting_source_id="disabled"
        )
    with pytest.raises(ValueError, match="canonical room"):
        resolve_alert_targets(
            household=_Household(), satellites=_Fleet(), scope="room",
            requesting_source_id="phil-a", target_id="garage",
        )
    with pytest.raises(ValueError, match="canonical users"):
        resolve_alert_targets(
            household=_Household(), satellites=_Fleet(), scope="recipient", requesting_source_id="common"
        )
