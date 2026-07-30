from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from oracle_app.configuration import (
    NormalizedBundle,
    TransitionValidationContext,
    canonicalize_json,
    load_bundle,
    normalize_bundle,
    validate_configuration_transition,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationTransitionValidationTests(unittest.TestCase):
    def test_enabled_optional_information_and_network_roles_require_prior_selected_disablement(self) -> None:
        previous = self._plain()
        previous["roles"]["domains/music.yaml"]["enabled"] = True
        previous["roles"]["domains/information.yaml"]["facts"]["enabled"] = True
        previous["roles"]["domains/network/inventory.yaml"]["enabled"] = True
        candidate = copy.deepcopy(previous)
        del candidate["roles"]["domains/music.yaml"]
        del candidate["roles"]["domains/information.yaml"]
        del candidate["roles"]["domains/network/adapters.yaml"]

        result = validate_configuration_transition(
            self._normalized(previous),
            self._normalized(candidate),
            context=self._context(),
        )

        codes = {item.code for item in result.blockers}
        self.assertEqual(
            codes,
            {
                "config.transition.enabled_role_removed",
                "config.transition.enabled_information_removed",
                "config.transition.enabled_network_removed",
            },
        )
        self.assertTrue(all(item.category == "activation" for item in result.blockers))

    def test_rekey_of_enabled_identity_is_a_removal(self) -> None:
        previous = self._plain()
        candidate = copy.deepcopy(previous)
        candidate["roles"]["household.yaml"]["users"][0]["id"] = "renamed_resident"
        candidate["roles"]["household.yaml"]["defaults"]["user_id"] = "renamed_resident"

        result = validate_configuration_transition(
            self._normalized(previous),
            self._normalized(candidate),
            context=self._context(),
        )

        self.assertEqual(result.blockers[0].code, "config.transition.enabled_identity_removed")
        self.assertIn("resident_one", result.blockers[0].path)

    @staticmethod
    def _plain() -> dict[str, object]:
        normalized = normalize_bundle(load_bundle(EXAMPLE_ROOT))
        return json.loads(normalized.canonical_bytes)

    @staticmethod
    def _normalized(configuration: dict[str, object]) -> NormalizedBundle:
        canonical = canonicalize_json(configuration)
        return NormalizedBundle(
            format="oracle-config-v1",
            config_revision="test-transition-revision",
            configuration=configuration,
            canonical_bytes=canonical,
        )

    @staticmethod
    def _context() -> TransitionValidationContext:
        return TransitionValidationContext(
            activation_generation_id="activation_" + "1" * 32,
            config_generation_id="config_" + "2" * 32,
            config_revision="oracle-config-v1:sha256:" + "3" * 64,
            selection_operation_id="selection_op_" + "4" * 32,
            selection_revision=4,
        )


if __name__ == "__main__":
    unittest.main()
