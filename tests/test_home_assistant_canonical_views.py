from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oracle_app.configuration.domain_models import (
    HomeAssistantCameraViewReference,
    HomeAssistantControlViewReference,
    HomeAssistantHomeView,
    HomeAssistantHouseView,
    HomeAssistantObjectMapping,
    HomeAssistantRoomView,
    HomeAssistantViewReference,
    HomeAssistantViews,
)
from oracle_app.configuration.home_assistant_action_semantics import (
    DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS,
)
from oracle_app.home_assistant_actions import (
    execute_home_assistant_ui_action,
    resolve_home_assistant_dynamic_ui_action,
)
from oracle_app.ui_house import build_canonical_ui_home_assistant_snapshot, build_ui_house_snapshot
from oracle_app.ui_satellite import build_satellite_room_controls_snapshot, build_satellite_ui_config
from oracle_app.health import check_home_assistant_health


class CanonicalHomeAssistantViewTests(unittest.TestCase):
    @patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
    def test_every_supported_direct_operation_is_executable_for_generic_action_ids(self, bridge_type) -> None:
        bridge = bridge_type.return_value
        cases = {
            "lock": ("entry_lock", "locked", "lock"),
            "turn_off": ("reading_light_off", "off", "light"),
            "turn_on": ("reading_light_on", "on", "light"),
            "unlock": ("entry_unlock", "unlocked", "lock"),
        }

        self.assertEqual(set(cases), set(DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS))
        for operation, (action_id, expected_state, domain) in cases.items():
            with self.subTest(operation=operation):
                mapping = HomeAssistantObjectMapping(
                    kind="action",
                    oracle_id="generic_target",
                    entity_id=f"{domain}.test_target",
                    allowed_operations=[operation],
                )
                settings = SimpleNamespace(
                    enabled=True,
                    base_url="http://canonical-ha.local",
                    credential="canonical-token",
                    timeout_seconds=12,
                    mapping=lambda candidate, expected=action_id, value=mapping: value if candidate == expected else None,
                )
                bridge.reset_mock()
                bridge.wait_for_entity_state.return_value = {"state": expected_state, "attributes": {}}

                result = execute_home_assistant_ui_action(
                    action_id,
                    home_assistant_settings=settings,
                    canonical_authority=True,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(result["ok"])
                bridge.call_service.assert_called_once_with(
                    service_domain=domain,
                    service_name=operation,
                    entity_id=f"{domain}.test_target",
                )

    @patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
    def test_unsupported_canonical_operation_remains_unavailable(self, bridge_type) -> None:
        mapping = HomeAssistantObjectMapping(
            kind="action",
            oracle_id="custom",
            entity_id="light.custom",
            allowed_operations=["toggle"],
        )
        settings = SimpleNamespace(
            enabled=True,
            base_url="http://canonical-ha.local",
            credential="canonical-token",
            timeout_seconds=12,
            mapping=lambda _candidate: mapping,
        )

        result = execute_home_assistant_ui_action(
            "custom_on",
            home_assistant_settings=settings,
            canonical_authority=True,
        )

        self.assertIsNone(result)
        bridge_type.assert_not_called()

    @patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
    def test_generic_canonical_climate_action_uses_mapping_operation(self, bridge_type) -> None:
        mapping = HomeAssistantObjectMapping(
            kind="action",
            oracle_id="reading_room_thermostat",
            entity_id="climate.reading_room",
            allowed_operations=["cooler"],
        )
        settings = SimpleNamespace(
            enabled=True,
            base_url="http://canonical-ha.local",
            credential="canonical-token",
            timeout_seconds=12,
            mapping=lambda candidate: mapping if candidate == "temperature_down" else None,
        )
        bridge_type.return_value.fetch_entity_state.return_value = {
            "state": "cool",
            "attributes": {"temperature": 70},
        }

        result = resolve_home_assistant_dynamic_ui_action(
            "temperature_down",
            home_assistant_settings=settings,
            canonical_authority=True,
        )

        self.assertEqual(
            result["command_text"],
            "set the reading room thermostat to 69 degrees",
        )
        bridge_type.return_value.fetch_entity_state.assert_called_once_with(
            "climate.reading_room"
        )

    @patch("oracle_app.ui_house.fetch_snapshot_metadata")
    @patch("oracle_app.ui_house.HomeAssistantBridge.fetch_entity_state")
    def test_house_view_uses_authored_order_and_typed_mappings_only(
        self,
        fetch_state,
        snapshot_metadata,
    ) -> None:
        fetch_state.side_effect = lambda entity_id: {
            "entity_id": entity_id,
            "state": "off" if entity_id.startswith("light.") else "idle",
            "attributes": {},
        }
        snapshot_metadata.return_value = SimpleNamespace(available=True)
        settings = self._settings()

        payload = build_ui_house_snapshot(
            home_assistant_settings=settings,
        )

        self.assertEqual([item["entity_id"] for item in payload["lights"]], ["light.second", "light.first"])
        self.assertEqual(payload["lights"][0]["label"], "Second Light")
        self.assertEqual(payload["cameras"][0]["camera_id"], "porch")
        self.assertEqual(payload["cameras"][0]["snapshot_url"].split("?", 1)[0], "/api/ui/house/cameras/porch/snapshot")
        snapshot_metadata.assert_called_once_with(
            base_url="http://canonical-ha.local",
            token="canonical-token",
            snapshot_path="/deployment/snapshots/porch.jpg",
            snapshot_root="/deployment/snapshots",
            timeout_seconds=12.0,
        )

    @patch("oracle_app.ui_house.HomeAssistantBridge.fetch_entity_state")
    def test_home_view_uses_only_configured_membership(self, fetch_state) -> None:
        fetch_state.return_value = {"state": "off", "attributes": {}}

        payload = build_canonical_ui_home_assistant_snapshot(self._settings())

        self.assertEqual([item["entity_id"] for item in payload["controls"]], ["light.first"])
        self.assertEqual([item["action_id"] for item in payload["actions"]], ["first_on"])

    @patch("oracle_app.ui_house.HomeAssistantBridge.fetch_entity_state")
    def test_room_view_uses_canonical_room_association_without_discovery(
        self,
        fetch_state,
    ) -> None:
        fetch_state.return_value = {"state": "off", "attributes": {}}
        fleet, household = self._fleet_and_household()

        config = build_satellite_ui_config(
            "living_room_satellite",
            fleet_settings=fleet,
            household_settings=household,
        )
        payload = build_satellite_room_controls_snapshot(
            "living_room_satellite",
            home_assistant_settings=self._settings(),
            fleet_settings=fleet,
            household_settings=household,
        )

        self.assertEqual(config["room_id"], "living_room")
        self.assertEqual(config["room"], "Living Room")
        self.assertEqual(payload["selection_source"], "canonical_view")
        self.assertEqual([item["entity_id"] for item in payload["items"]], ["light.second"])

    @staticmethod
    def _settings():
        mappings = {
            "first": HomeAssistantObjectMapping(kind="entity", oracle_id="first", entity_id="light.first", allowed_operations=["read"]),
            "second": HomeAssistantObjectMapping(kind="entity", oracle_id="second", entity_id="light.second", allowed_operations=["read"]),
            "first_on": HomeAssistantObjectMapping(kind="action", oracle_id="first", entity_id="light.first", allowed_operations=["turn_on"]),
            "second_on": HomeAssistantObjectMapping(kind="action", oracle_id="second", entity_id="light.second", allowed_operations=["turn_on"]),
            "porch_camera": HomeAssistantObjectMapping(kind="camera", oracle_id="porch", entity_id="camera.porch", allowed_operations=["read"]),
        }
        return SimpleNamespace(
            enabled=True,
            base_url="http://canonical-ha.local",
            credential="canonical-token",
            timeout_seconds=12,
            snapshot_root="/deployment/snapshots",
            views=HomeAssistantViews(
                home=HomeAssistantHomeView(
                    controls=[HomeAssistantControlViewReference(mapping_id="first", action_ids=["first_on"])],
                    actions=[HomeAssistantViewReference(mapping_id="first_on", label="First On")],
                ),
                house=HomeAssistantHouseView(
                    lights=[
                        HomeAssistantControlViewReference(mapping_id="second", label="Second Light", action_ids=["second_on"]),
                        HomeAssistantControlViewReference(mapping_id="first", label="First Light", action_ids=["first_on"]),
                    ],
                    cameras=[HomeAssistantCameraViewReference(mapping_id="porch_camera", label="Porch", snapshot_ref="porch.jpg")],
                ),
                rooms={
                    "living_room": HomeAssistantRoomView(
                        controls=[HomeAssistantControlViewReference(mapping_id="second", label="Living Room")]
                    )
                },
            ),
            mapping=lambda mapping_id: mappings.get(mapping_id),
        )

    @staticmethod
    def _fleet_and_household():
        ui = SimpleNamespace(
            enabled=True,
            touch=True,
            profile="living_room_touch_v1",
            layout="satellite_landscape_touch_v1",
            pages=["home", "weather", "house"],
            bottom_nav=["home", "weather", "house"],
        )
        satellite = SimpleNamespace(
            satellite_id="living_room_satellite",
            enabled=True,
            source_id="living_room_source",
            ui=ui,
            capabilities=SimpleNamespace(voice=True, music_playback=True, audiobook_playback=True, display=True),
        )
        fleet = SimpleNamespace(
            entries={satellite.satellite_id: satellite},
            entry=lambda value: satellite if value in {satellite.satellite_id, satellite.source_id} else None,
        )
        source = SimpleNamespace(id="living_room_source")
        room = SimpleNamespace(id="living_room", display_name="Living Room")
        household = SimpleNamespace(
            source=lambda value: source if value == source.id else None,
            configured_associated_room_id=lambda value: room.id if value == source.id else None,
            room=lambda value: room if value == room.id else None,
        )
        return fleet, household


class HomeAssistantSnapshotReferenceTests(unittest.TestCase):
    def test_snapshot_reference_rejects_absolute_and_parent_paths(self) -> None:
        for value in ("/absolute.jpg", "../escape.jpg", "nested/../../escape.jpg"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HomeAssistantCameraViewReference(mapping_id="camera", snapshot_ref=value)

    @patch("oracle_app.health.request.urlopen")
    @patch("oracle_app.health.get_home_assistant_settings")
    def test_canonical_health_uses_typed_provider_and_timeout(self, legacy_settings, urlopen) -> None:
        class Response:
            status = 200

            def read(self):
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        urlopen.return_value = Response()
        settings = SimpleNamespace(
            enabled=True,
            base_url="http://canonical-ha.local",
            credential="canonical-token",
            timeout_seconds=14,
        )

        result = check_home_assistant_health(settings, canonical_authority=True)

        self.assertEqual(result.status, "ok")
        legacy_settings.assert_not_called()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 14.0)


if __name__ == "__main__":
    unittest.main()
