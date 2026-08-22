from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    SecretCompanionError,
    inspect_candidate,
    load_secret_companion,
    parse_secret_companion,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationSecretTests(unittest.TestCase):
    def test_parser_preserves_values_as_non_shell_data(self) -> None:
        snapshot = parse_secret_companion(
            "# comment\n"
            "  # indented comment\n"
            " TOKEN_ONE =quoted '$VALUE' \\\n"
            "TOKEN_TWO=a=b=c\n"
            "EMPTY=\n"
        )

        self.assertEqual(snapshot.present_ids, frozenset({"TOKEN_ONE", "TOKEN_TWO"}))
        self.assertEqual(snapshot.resolve("TOKEN_ONE"), "quoted '$VALUE' \\")
        self.assertEqual(snapshot.resolve("TOKEN_TWO"), "a=b=c")
        self.assertIsNone(snapshot.resolve("EMPTY"))
        self.assertNotIn("quoted", repr(snapshot))

    def test_parser_rejects_shell_syntax_malformed_keys_duplicates_and_invalid_utf8(self) -> None:
        cases = (
            "export TOKEN=value\n",
            "token=value\n",
            "TOKEN\n",
            "TOKEN=one\nTOKEN=two\n",
            "\ufeffTOKEN=value\n",
            b"TOKEN=\xff\n",
        )
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(SecretCompanionError):
                    parse_secret_companion(data)

    def test_companion_path_is_fixed_and_confined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "config"
            root.mkdir()
            outside = parent / "outside.env"
            outside.write_text("TOKEN=value\n", encoding="utf-8")
            (root / "secrets.env").symlink_to(outside)

            with self.assertRaises(SecretCompanionError) as caught:
                load_secret_companion(root)

            self.assertEqual(caught.exception.code, "config.secret.path_escape")

    def test_enabled_source_missing_secret_is_activation_blocker_not_validation_error(self) -> None:
        with self._bundle_copy() as root:
            self._add_mobile_source_and_binding(root, enabled=True)

            inspection = inspect_candidate(root)

            self.assertFalse(inspection.report.activation_eligible)
            self.assertEqual(inspection.report.validation_findings, ())
            self.assertEqual(len(inspection.report.activation_blockers), 1)
            blocker = inspection.report.activation_blockers[0]
            self.assertEqual(blocker.code, "config.secret.required_missing")
            self.assertEqual(blocker.category, "activation")
            self.assertNotIn("secret-value", blocker.message)

    def test_present_required_secret_allows_activation_without_entering_revision(self) -> None:
        with self._bundle_copy() as root:
            self._add_mobile_source_and_binding(root, enabled=True)
            before = inspect_candidate(root)
            (root / "secrets.env").write_text("RESIDENT_PHONE_TOKEN=secret-value\n", encoding="utf-8")
            after = inspect_candidate(root)

            self.assertFalse(before.report.activation_eligible)
            self.assertTrue(after.report.activation_eligible)
            self.assertEqual(before.normalized.config_revision, after.normalized.config_revision)
            self.assertNotIn(b"secret-value", after.normalized.canonical_bytes)
            self.assertNotIn("secret-value", repr(after.secrets))

    def test_disabled_reference_may_be_absent_and_unreferenced_secret_warns(self) -> None:
        with self._bundle_copy() as root:
            self._add_mobile_source_and_binding(root, enabled=False)
            (root / "secrets.env").write_text("UNUSED_TOKEN=secret-value\n", encoding="utf-8")

            inspection = inspect_candidate(root)

            self.assertTrue(inspection.report.activation_eligible)
            self.assertEqual(inspection.report.activation_blockers, ())
            self.assertEqual(len(inspection.report.validation_findings), 1)
            warning = inspection.report.validation_findings[0]
            self.assertEqual(warning.code, "config.secret.unreferenced")
            self.assertEqual(warning.severity, "warning")
            self.assertFalse(warning.blocks_activation)
            self.assertNotIn("secret-value", warning.message)

    def test_empty_required_value_is_absent(self) -> None:
        with self._bundle_copy() as root:
            self._add_mobile_source_and_binding(root, enabled=True)
            (root / "secrets.env").write_text("RESIDENT_PHONE_TOKEN=\n", encoding="utf-8")

            inspection = inspect_candidate(root)

            self.assertFalse(inspection.report.activation_eligible)
            self.assertEqual(inspection.report.activation_blockers[0].code, "config.secret.required_missing")

    def test_selected_domain_provider_secret_is_required_but_not_hashed(self) -> None:
        with self._bundle_copy() as root:
            music = root / "domains" / "music.yaml"
            music.write_text(
                music.read_text(encoding="utf-8").replace("enabled: false\nprovider: null", "enabled: true\nprovider: plex"),
                encoding="utf-8",
            )
            before = inspect_candidate(root)
            self.assertEqual(
                [(item.file_role, item.path) for item in before.report.activation_blockers],
                [("domains/music.yaml", "providers.plex.credential_secret")],
            )

            (root / "secrets.env").write_text("PLEX_TOKEN=domain-secret\n", encoding="utf-8")
            after = inspect_candidate(root)

            self.assertTrue(after.report.activation_eligible)
            self.assertEqual(before.normalized.config_revision, after.normalized.config_revision)
            self.assertNotIn(b"domain-secret", after.normalized.canonical_bytes)

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

    @staticmethod
    def _add_mobile_source_and_binding(root: Path, *, enabled: bool) -> None:
        household_path = root / "household.yaml"
        household = household_path.read_text(encoding="utf-8")
        household_path.write_text(
            household.replace(
                "sources: []",
                "sources:\n"
                "  - id: resident_phone\n"
                f"    enabled: {'true' if enabled else 'false'}\n"
                "    type: mobile_app\n"
                "    fixed: false",
            ),
            encoding="utf-8",
        )
        access_path = root / "access.yaml"
        access_path.write_text(
            access_path.read_text(encoding="utf-8")
            + "source_authentication:\n"
            + "  credential_bindings:\n"
            + "    - source_id: resident_phone\n"
            + "      credential_secret: RESIDENT_PHONE_TOKEN\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
