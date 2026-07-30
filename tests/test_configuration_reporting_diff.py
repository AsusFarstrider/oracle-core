from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import BundleValidationError, inspect_candidate, load_bundle, normalize_bundle, semantic_diff


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationReportingAndDiffTests(unittest.TestCase):
    def test_valid_candidate_report_separates_categories_and_records_provenance(self) -> None:
        inspection = inspect_candidate(EXAMPLE_ROOT)

        self.assertTrue(inspection.report.activation_eligible)
        self.assertEqual(inspection.report.validation_findings, ())
        self.assertEqual(inspection.report.activation_blockers, ())
        self.assertEqual(inspection.report.readiness_findings, ())
        self.assertIsNotNone(inspection.normalized)
        provenance = {(item.file_role, item.path): item.source for item in inspection.provenance}
        self.assertEqual(provenance[("access.yaml", "operator_access.mode")], "authored")
        self.assertEqual(provenance[("access.yaml", "operator_access.browser_inspection")], "defaulted")

    def test_invalid_candidate_returns_validation_errors_not_activation_or_readiness(self) -> None:
        with self._bundle_copy() as root:
            (root / "bundle.yaml").write_text("kind: wrong\n", encoding="utf-8")

            inspection = inspect_candidate(root)

            self.assertFalse(inspection.report.activation_eligible)
            self.assertTrue(inspection.report.validation_findings)
            self.assertEqual(inspection.report.activation_blockers, ())
            self.assertEqual(inspection.report.readiness_findings, ())
            self.assertIsNone(inspection.bundle)
            for finding in inspection.report.validation_findings:
                self.assertEqual(finding.category, "validation")
                self.assertEqual(finding.severity, "error")
                self.assertTrue(finding.blocks_activation)

    def test_semantically_invalid_candidate_retains_normalized_revision_and_graph(self) -> None:
        with self._bundle_copy() as root:
            household = root / "household.yaml"
            household.write_text(
                household.read_text(encoding="utf-8").replace(
                    "user_id: resident_one",
                    "user_id: missing_resident",
                ),
                encoding="utf-8",
            )

            inspection = inspect_candidate(root)

            self.assertFalse(inspection.report.activation_eligible)
            self.assertTrue(
                any(
                    item.code == "config.reference.unknown_default_user"
                    for item in inspection.report.validation_findings
                )
            )
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertEqual(inspection.normalized_candidate_revision, inspection.normalized.config_revision)
            self.assertIsNotNone(inspection.bundle)
            self.assertTrue(inspection.provenance)
            with self.assertRaises(BundleValidationError):
                load_bundle(root)

    def test_semantic_diff_is_empty_for_equivalent_authored_order(self) -> None:
        baseline = normalize_bundle(load_bundle(EXAMPLE_ROOT))
        with self._bundle_copy() as root:
            access = root / "access.yaml"
            access.write_text(
                "public_health:\n"
                "  enabled: false\n"
                "operator_access:\n"
                "  browser_inspection: false\n"
                "  browser_mutation: false\n"
                "  host_local_cli: true\n"
                "  mode: host_local_only\n"
                "satellite_authentication:\n"
                "  directional_credentials_required: true\n"
                "  enrollment_mode: per_satellite\n",
                encoding="utf-8",
            )

            self.assertEqual(semantic_diff(baseline, normalize_bundle(load_bundle(root))), ())

    def test_diff_classifies_access_expansion_and_restart_impact(self) -> None:
        baseline = normalize_bundle(load_bundle(EXAMPLE_ROOT))
        with self._bundle_copy() as root:
            (root / "access.yaml").write_text(
                "operator_access:\n"
                "  mode: trusted_boundary\n"
                "  boundary_id: oracle_web_gateway\n"
                "  browser_mutation: true\n"
                "  csrf_protection: boundary_proof\n"
                "  host_local_cli: true\n"
                "trusted_boundary:\n"
                "  boundary_id: oracle_web_gateway\n"
                "  enabled: true\n"
                "  type: authenticated_reverse_proxy\n"
                "  trusted_proxy_ids: [oracle-web-gateway]\n"
                "  accepted_headers: [authenticated_request]\n"
                "public_health:\n"
                "  enabled: true\n"
                "satellite_authentication:\n"
                "  enrollment_mode: per_satellite\n"
                "  directional_credentials_required: true\n",
                encoding="utf-8",
            )

            changes = semantic_diff(baseline, normalize_bundle(load_bundle(root)))

            acknowledgements = {item for change in changes for item in change.safety_acknowledgements}
            self.assertIn("access_expansion", acknowledgements)
            self.assertIn("public_health_enablement", acknowledgements)
            self.assertTrue(all(change.restart_required for change in changes))

    def test_diff_tracks_identity_removal_by_id_not_list_index(self) -> None:
        with self._bundle_copy() as root:
            household = root / "household.yaml"
            text = household.read_text(encoding="utf-8")
            text = text.replace(
                "defaults:\n",
                "  - id: resident_two\n"
                "    enabled: false\n"
                "    display_name: Resident Two\n"
                "    aliases: []\n"
                "    capabilities: {}\n"
                "defaults:\n",
            )
            household.write_text(text, encoding="utf-8")
            before = normalize_bundle(load_bundle(root))
            household.write_text(text.replace(
                "  - id: resident_two\n"
                "    enabled: false\n"
                "    display_name: Resident Two\n"
                "    aliases: []\n"
                "    capabilities: {}\n",
                "",
            ), encoding="utf-8")
            after = normalize_bundle(load_bundle(root))

            changes = semantic_diff(before, after)

            removal = next(change for change in changes if "users[id=resident_two]" in change.path)
            self.assertEqual(removal.operation, "remove")
            self.assertIn("identity_removal", removal.safety_acknowledgements)

    def _bundle_copy(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "config"
        shutil.copytree(EXAMPLE_ROOT, root)

        class BundleContext:
            def __enter__(self_nonlocal):
                return root

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return BundleContext()


if __name__ == "__main__":
    unittest.main()
