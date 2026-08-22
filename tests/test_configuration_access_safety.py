from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from oracle_app.configuration import (
    ACCESS_SCHEMA_FIELD_DISPOSITIONS,
    access_schema_field_paths,
    classify_access_safety,
    intrinsic_access_acknowledgements,
    load_bundle,
    normalize_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationAccessSafetyTests(unittest.TestCase):
    def test_every_current_access_schema_field_has_an_explicit_disposition(self) -> None:
        self.assertEqual(set(ACCESS_SCHEMA_FIELD_DISPOSITIONS), set(access_schema_field_paths()))

    def test_security_relevant_access_expansions_are_table_classified(self) -> None:
        cases = (
            ("browser inspection", self._browser_inspection, {"access_expansion"}),
            ("browser mutation", self._browser_mutation, {"access_expansion"}),
            ("trusted operator mode", self._trusted_mode, {"access_expansion"}),
            ("enable declared boundary", self._enable_boundary, {"access_expansion"}),
            ("replace active boundary ID", self._replace_boundary_id, {"access_expansion"}),
            ("add trusted proxy", self._add_trusted_proxy, {"access_expansion"}),
            ("enable public health", self._public_health, {"public_health_enablement"}),
            ("add active source binding", self._add_active_binding, {"access_expansion"}),
            ("change active credential role", self._change_active_credential, {"credential_role_change"}),
        )
        for label, mutation, expected in cases:
            with self.subTest(label=label):
                before = self._configuration()
                after = copy.deepcopy(before)
                mutation(before, after)
                classified = classify_access_safety(before, after)
                actual = {item for values in classified.values() for item in values}
                self.assertTrue(expected.issubset(actual), classified)

    def test_restrictive_access_changes_are_not_mislabeled_as_expansion(self) -> None:
        after = self._configuration()
        before = copy.deepcopy(after)
        before["roles"]["access.yaml"]["operator_access"]["browser_inspection"] = True
        classified = classify_access_safety(before, after)
        self.assertNotIn("access_expansion", {item for values in classified.values() for item in values})

    def test_initial_access_acknowledgements_cover_inspection_and_active_bindings(self) -> None:
        configuration = self._configuration()
        configuration["roles"]["access.yaml"]["operator_access"]["browser_inspection"] = True
        self._add_active_binding(self._configuration(), configuration)
        self.assertEqual(intrinsic_access_acknowledgements(configuration), frozenset({"access_expansion"}))

    @staticmethod
    def _configuration() -> dict[str, object]:
        return json.loads(normalize_bundle(load_bundle(EXAMPLE_ROOT)).canonical_bytes)

    @staticmethod
    def _browser_inspection(_before, after) -> None:
        after["roles"]["access.yaml"]["operator_access"]["browser_inspection"] = True

    @staticmethod
    def _browser_mutation(_before, after) -> None:
        after["roles"]["access.yaml"]["operator_access"]["browser_mutation"] = True

    @staticmethod
    def _trusted_mode(_before, after) -> None:
        after["roles"]["access.yaml"]["operator_access"]["mode"] = "trusted_boundary"

    @staticmethod
    def _declared_boundary(enabled: bool, boundary_id: str = "gateway") -> dict[str, object]:
        return {
            "boundary_id": boundary_id,
            "enabled": enabled,
            "type": "authenticated_reverse_proxy",
            "trusted_proxy_ids": ["proxy_one"],
            "accepted_headers": ["authenticated_request"],
        }

    @classmethod
    def _enable_boundary(cls, before, after) -> None:
        before["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(False)
        after["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(True)

    @classmethod
    def _replace_boundary_id(cls, before, after) -> None:
        before["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(True, "old_gateway")
        after["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(True, "new_gateway")

    @classmethod
    def _add_trusted_proxy(cls, before, after) -> None:
        before["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(True)
        after["roles"]["access.yaml"]["trusted_boundary"] = cls._declared_boundary(True)
        after["roles"]["access.yaml"]["trusted_boundary"]["trusted_proxy_ids"].append("proxy_two")

    @staticmethod
    def _public_health(_before, after) -> None:
        after["roles"]["access.yaml"]["public_health"]["enabled"] = True

    @staticmethod
    def _add_active_binding(_before, after) -> None:
        after["roles"]["household.yaml"]["sources"] = [
            {"id": "phone", "enabled": True, "type": "mobile_app", "fixed": False}
        ]
        after["roles"]["access.yaml"]["source_authentication"] = {
            "credential_bindings": [{"source_id": "phone", "credential_secret": "PHONE_TOKEN"}]
        }

    @classmethod
    def _change_active_credential(cls, before, after) -> None:
        cls._add_active_binding(before, before)
        cls._add_active_binding(after, after)
        after["roles"]["access.yaml"]["source_authentication"]["credential_bindings"][0][
            "credential_secret"
        ] = "REPLACEMENT_PHONE_TOKEN"


if __name__ == "__main__":
    unittest.main()
