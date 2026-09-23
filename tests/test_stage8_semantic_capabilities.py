from __future__ import annotations

import unittest

from oracle_app.capabilities.preauthorization import (
    PreauthorizedPower,
    SafetyManifest,
    build_safety_manifest,
    required_safety_acknowledgements,
)
from oracle_app.capabilities.semantic import (
    BlastRadius,
    CapabilityOutcome,
    CapabilityResult,
    ConfirmationClass,
    InvalidCapabilityArgumentsError,
    SEMANTIC_CAPABILITY_REGISTRY,
    SemanticDirection,
    UnknownSemanticCapabilityError,
)
from oracle_app.configuration.safety_acknowledgements import (
    KNOWN_SAFETY_ACKNOWLEDGEMENTS,
    RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT,
)
from oracle_app.configuration.service import ConfigurationService


class SemanticCapabilityRegistryTests(unittest.TestCase):
    def test_registry_is_closed_and_unknown_capabilities_fail_closed(self) -> None:
        self.assertEqual(
            SEMANTIC_CAPABILITY_REGISTRY.capability_ids,
            (
                "alerts.sound",
                "audiobooks.sleep_timer.set",
                "audiobooks.start_current",
                "home.access.set",
                "home.environment.setpoint",
                "home.lights.set",
                "home.power.set",
                "home.provider_action.invoke",
                "notifications.submit",
                "notifications.trigger",
            ),
        )
        with self.assertRaises(UnknownSemanticCapabilityError):
            SEMANTIC_CAPABILITY_REGISTRY.require("provider.raw.execute")

    def test_typed_arguments_are_bounded_and_closed(self) -> None:
        lights = SEMANTIC_CAPABILITY_REGISTRY.require("home.lights.set")
        self.assertEqual(
            dict(lights.validate_arguments({"target_id": "lighting_group", "state": "on"})),
            {"target_id": "lighting_group", "state": "on"},
        )
        for invalid in (
            {"target_id": "provider.raw", "state": "on"},
            {"target_id": "lighting_group", "state": "toggle"},
            {"target_id": "lighting_group", "state": "on", "service": "turn_on"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidCapabilityArgumentsError):
                lights.validate_arguments(invalid)

        notification = SEMANTIC_CAPABILITY_REGISTRY.require("notifications.submit")
        with self.assertRaises(InvalidCapabilityArgumentsError):
            notification.validate_arguments(
                {"notification_id": "notification_target", "message": "x" * 501}
            )

    def test_result_vocabulary_and_schema_are_bounded(self) -> None:
        lights = SEMANTIC_CAPABILITY_REGISTRY.require("home.lights.set")
        result = lights.validate_result({"outcome": "verified", "verified_state": "off"})
        self.assertEqual(dict(result), {"outcome": "verified", "verified_state": "off"})
        with self.assertRaises(InvalidCapabilityArgumentsError):
            lights.validate_result(
                {"outcome": "verified", "verified_state": "off", "provider_payload": {"token": "secret"}}
            )

    def test_risk_is_invariant_across_interfaces(self) -> None:
        arguments = {"target_id": "access_point", "state": "unlocked"}
        decisions = {
            SEMANTIC_CAPABILITY_REGISTRY.assess(
                "home.access.set", arguments, interface=interface
            )
            for interface in ("voice", "ui", "runbook", "system_mode")
        }
        self.assertEqual(
            decisions,
            {(SemanticDirection.EXPOSE, ConfirmationClass.CONSEQUENTIAL)},
        )
        self.assertEqual(
            SEMANTIC_CAPABILITY_REGISTRY.assess(
                "home.access.set",
                {"target_id": "access_point", "state": "locked"},
                interface="voice",
            ),
            (SemanticDirection.SECURE, ConfirmationClass.ORDINARY),
        )

    def test_direction_blast_radius_and_execution_traits_are_explicit(self) -> None:
        expected = {
            "home.lights.set": ({SemanticDirection.ACTIVATE, SemanticDirection.DEACTIVATE}, BlastRadius.HOUSEHOLD),
            "home.access.set": ({SemanticDirection.SECURE, SemanticDirection.EXPOSE}, BlastRadius.TARGET),
            "home.provider_action.invoke": ({SemanticDirection.EXECUTE}, BlastRadius.HOUSEHOLD),
            "audiobooks.start_current": ({SemanticDirection.ACTIVATE}, BlastRadius.TARGET),
            "audiobooks.sleep_timer.set": ({SemanticDirection.ADJUST}, BlastRadius.TARGET),
            "alerts.sound": ({SemanticDirection.COMMUNICATE}, BlastRadius.TARGET),
            "notifications.submit": ({SemanticDirection.COMMUNICATE}, BlastRadius.HOUSEHOLD),
            "notifications.trigger": ({SemanticDirection.COMMUNICATE}, BlastRadius.HOUSEHOLD),
        }
        for capability_id, (directions, maximum) in expected.items():
            with self.subTest(capability_id=capability_id):
                definition = SEMANTIC_CAPABILITY_REGISTRY.require(capability_id)
                self.assertEqual({item for _, item in definition.directions}, directions)
                self.assertEqual(definition.maximum_blast_radius, maximum)
                self.assertTrue(definition.blast_radius_inputs)
                self.assertIsInstance(definition.safe_for_bounded_retry, bool)
        opaque = SEMANTIC_CAPABILITY_REGISTRY.require("home.provider_action.invoke")
        self.assertTrue(opaque.opaque_provider_unit)
        self.assertFalse(hasattr(opaque, "provider_steps"))

    def test_sanitized_outcome_cannot_carry_arguments_or_provider_payload(self) -> None:
        result = CapabilityResult(
            capability_id="notifications.submit",
            outcome=CapabilityOutcome.ACCEPTED,
            message_code="notification_accepted",
            operation_id="notification_op_1",
        )
        audit = result.sanitized_audit()
        self.assertEqual(set(audit), {"capability_id", "outcome", "message_code", "operation_id"})
        self.assertNotIn("arguments", audit)
        self.assertNotIn("provider_payload", audit)


class SafetyManifestTests(unittest.TestCase):
    @staticmethod
    def _access_power(*, triggers: tuple[str, ...] = ()) -> PreauthorizedPower:
        return PreauthorizedPower(
            capability_id="home.access.set",
            semantic_direction=SemanticDirection.EXPOSE,
            blast_radius=BlastRadius.TARGET,
            target_ids=("access_point",),
            automatic_triggers=triggers,
        )

    def test_manifest_hash_is_stable_and_order_independent(self) -> None:
        access = self._access_power()
        lights = PreauthorizedPower(
            capability_id="home.lights.set",
            semantic_direction=SemanticDirection.DEACTIVATE,
            blast_radius=BlastRadius.ROOM,
            target_ids=("lighting_group",),
        )
        first = build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, (access, lights))
        second = build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, (lights, access))
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.to_sanitized_dict(), second.to_sanitized_dict())

    def test_unknown_excessive_or_duplicate_power_fails_closed(self) -> None:
        with self.assertRaises(UnknownSemanticCapabilityError):
            build_safety_manifest(
                SEMANTIC_CAPABILITY_REGISTRY,
                (
                    PreauthorizedPower(
                        capability_id="provider.raw.execute",
                        semantic_direction=SemanticDirection.EXECUTE,
                        blast_radius=BlastRadius.TARGET,
                        target_ids=("target",),
                    ),
                ),
            )
        excessive = PreauthorizedPower(
            capability_id="home.access.set",
            semantic_direction=SemanticDirection.EXPOSE,
            blast_radius=BlastRadius.HOUSEHOLD,
            target_ids=("access_point",),
        )
        with self.assertRaises(ValueError):
            build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, (excessive,))
        duplicate = self._access_power()
        with self.assertRaises(ValueError):
            build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, (duplicate, duplicate))

    def test_forged_manifest_is_rejected_before_acknowledgement_classification(self) -> None:
        valid = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY, (self._access_power(),)
        )
        forged = SafetyManifest(
            format=valid.format,
            powers=valid.powers,
            digest="sha256:" + ("0" * 64),
        )
        with self.assertRaises(ValueError):
            required_safety_acknowledgements(
                SEMANTIC_CAPABILITY_REGISTRY, None, forged
            )

    def test_material_expansion_reuses_configuration_acknowledgement(self) -> None:
        ordinary = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (
                PreauthorizedPower(
                    capability_id="home.lights.set",
                    semantic_direction=SemanticDirection.DEACTIVATE,
                    blast_radius=BlastRadius.ROOM,
                    target_ids=("lighting_group",),
                ),
            ),
        )
        consequential = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (*ordinary.powers, self._access_power()),
        )
        self.assertEqual(
            required_safety_acknowledgements(
                SEMANTIC_CAPABILITY_REGISTRY, ordinary, consequential
            ),
            frozenset({RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT}),
        )
        self.assertEqual(
            required_safety_acknowledgements(
                SEMANTIC_CAPABILITY_REGISTRY, consequential, ordinary
            ),
            frozenset(),
        )
        self.assertIn(
            RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT,
            KNOWN_SAFETY_ACKNOWLEDGEMENTS,
        )
        ConfigurationService._validate_acknowledgements(
            frozenset({RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT})
        )

    def test_automatic_trigger_expansion_is_material_even_for_ordinary_action(self) -> None:
        manual = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (
                PreauthorizedPower(
                    capability_id="notifications.submit",
                    semantic_direction=SemanticDirection.COMMUNICATE,
                    blast_radius=BlastRadius.TARGET,
                    target_ids=("notification_target",),
                ),
            ),
        )
        automatic = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (
                PreauthorizedPower(
                    capability_id="notifications.submit",
                    semantic_direction=SemanticDirection.COMMUNICATE,
                    blast_radius=BlastRadius.TARGET,
                    target_ids=("notification_target",),
                    automatic_triggers=("home_event",),
                ),
            ),
        )
        self.assertEqual(
            required_safety_acknowledgements(
                SEMANTIC_CAPABILITY_REGISTRY, manual, automatic
            ),
            frozenset({RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT}),
        )

    def test_blast_radius_or_target_expansion_is_material(self) -> None:
        initial = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (
                PreauthorizedPower(
                    capability_id="home.lights.set",
                    semantic_direction=SemanticDirection.DEACTIVATE,
                    blast_radius=BlastRadius.TARGET,
                    target_ids=("lighting_target",),
                ),
            ),
        )
        expanded = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY,
            (
                PreauthorizedPower(
                    capability_id="home.lights.set",
                    semantic_direction=SemanticDirection.DEACTIVATE,
                    blast_radius=BlastRadius.ROOM,
                    target_ids=("lighting_group", "lighting_target"),
                ),
            ),
        )
        self.assertEqual(
            required_safety_acknowledgements(
                SEMANTIC_CAPABILITY_REGISTRY, initial, expanded
            ),
            frozenset({RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT}),
        )

    def test_manifest_contains_no_arguments_secrets_or_provider_payload(self) -> None:
        manifest = build_safety_manifest(
            SEMANTIC_CAPABILITY_REGISTRY, (self._access_power(triggers=("home_event",)),)
        )
        serialized = str(manifest.to_sanitized_dict()).lower()
        self.assertNotIn("arguments", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("provider", serialized)


if __name__ == "__main__":
    unittest.main()
