from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    CONFIG_FORMAT,
    CONFIG_REVISION_PREFIX,
    canonicalize_json,
    load_bundle,
    normalize_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationNormalizationTests(unittest.TestCase):
    def test_rfc8785_golden_canonical_bytes(self) -> None:
        value = {
            "z": "Ω",
            "a": [None, True, False, 1, 1.5, "line\nfeed"],
            "nested": {"b": 2, "a": 1},
        }

        self.assertEqual(
            canonicalize_json(value),
            b'{"a":[null,true,false,1,1.5,"line\\nfeed"],"nested":{"a":1,"b":2},"z":"\xce\xa9"}',
        )

    def test_complete_example_has_stable_revision_and_envelope(self) -> None:
        normalized = normalize_bundle(load_bundle(EXAMPLE_ROOT))

        self.assertEqual(normalized.format, CONFIG_FORMAT)
        self.assertTrue(normalized.config_revision.startswith(CONFIG_REVISION_PREFIX))
        self.assertEqual(
            normalized.config_revision,
            "oracle-config-v2:sha256:61939728c64133a8b96fc256c3522f5160e932c421b2a0b060173490fa9339f5",
        )
        self.assertEqual(hashlib.sha256(normalized.canonical_bytes).hexdigest(), normalized.config_revision.rsplit(":", 1)[1])
        self.assertEqual(normalized.configuration["kind"], "oracle_configuration_bundle")
        self.assertNotIn("bundle.yaml", normalized.configuration["roles"])
        self.assertEqual(normalized.envelope()["configuration"]["kind"], normalized.configuration["kind"])

    def test_normalized_graph_is_immutable_and_envelope_is_a_copy(self) -> None:
        normalized = normalize_bundle(load_bundle(EXAMPLE_ROOT))

        with self.assertRaises(TypeError):
            normalized.configuration["kind"] = "changed"  # type: ignore[index]
        envelope = normalized.envelope()
        envelope["configuration"]["kind"] = "changed"
        self.assertEqual(normalized.configuration["kind"], "oracle_configuration_bundle")

    def test_comments_order_and_explicit_safe_defaults_do_not_change_revision(self) -> None:
        with self._bundle_copy() as root:
            access_path = root / "access.yaml"
            access_path.write_text(
                "# reordered without semantic change\n"
                "satellite_authentication:\n"
                "  directional_credentials_required: true\n"
                "  enrollment_mode: per_satellite\n"
                "public_health:\n"
                "  enabled: false\n"
                "operator_access:\n"
                "  browser_inspection: false\n"
                "  host_local_cli: true\n"
                "  browser_mutation: false\n"
                "  mode: host_local_only\n",
                encoding="utf-8",
            )

            self.assertEqual(
                normalize_bundle(load_bundle(root)).config_revision,
                normalize_bundle(load_bundle(EXAMPLE_ROOT)).config_revision,
            )

    def test_optional_role_presence_is_semantic_but_non_authoritative_files_are_not(self) -> None:
        baseline = normalize_bundle(load_bundle(EXAMPLE_ROOT)).config_revision
        with self._bundle_copy() as root:
            (root / "operator-notes.txt").write_text("not configuration\n", encoding="utf-8")
            (root / "secrets.env.example").write_text("DIFFERENT=<value>\n", encoding="utf-8")
            self.assertEqual(normalize_bundle(load_bundle(root)).config_revision, baseline)

            (root / "domains" / "routines.yaml").unlink()
            self.assertNotEqual(normalize_bundle(load_bundle(root)).config_revision, baseline)

    def test_identity_and_alias_order_are_not_semantic(self) -> None:
        with self._bundle_copy() as root:
            household = root / "household.yaml"
            household.write_text(
                "household:\n"
                "  id: example_home\n"
                "  display_name: Example Home\n"
                "  timezone: Etc/UTC\n"
                "  locale: en-US\n"
                "users:\n"
                "  - id: resident_two\n"
                "    enabled: false\n"
                "    display_name: Resident Two\n"
                "    aliases: [Second, two]\n"
                "    capabilities: {}\n"
                "  - id: resident_one\n"
                "    enabled: true\n"
                "    display_name: Resident One\n"
                "    aliases: []\n"
                "    capabilities: {}\n"
                "defaults:\n"
                "  user_id: resident_one\n"
                "rooms:\n"
                "  - id: living_room\n"
                "    enabled: true\n"
                "    display_name: Living Room\n"
                "    aliases: [lounge]\n"
                "sources: []\n"
                "modes: []\n",
                encoding="utf-8",
            )
            first = normalize_bundle(load_bundle(root)).config_revision
            text = household.read_text(encoding="utf-8")
            text = text.replace("aliases: [Second, two]", "aliases: [two, Second]")
            first_user = text.index("  - id: resident_two")
            defaults = text.index("defaults:")
            users_block = text[first_user:defaults]
            two = users_block.index("  - id: resident_two")
            one = users_block.index("  - id: resident_one")
            resident_two = users_block[two:one]
            resident_one = users_block[one:]
            household.write_text(text[:first_user] + resident_one + resident_two + text[defaults:], encoding="utf-8")

            self.assertEqual(normalize_bundle(load_bundle(root)).config_revision, first)

    def test_domain_identity_order_is_not_semantic_but_routine_step_order_is(self) -> None:
        with self._bundle_copy() as root:
            notifications = root / "domains" / "notifications.yaml"
            text = notifications.read_text(encoding="utf-8")
            second = (
                "  - id: another_notice\n"
                "    enabled: false\n"
                "    message: Another disabled example.\n"
                "    audience: []\n"
                "    suppressed_by: []\n"
                "    delivery_ttl_seconds: 90\n"
                "    audio_policy: pause_resume\n"
            )
            notifications.write_text(text.replace("types:\n", "types:\n" + second), encoding="utf-8")
            first_revision = normalize_bundle(load_bundle(root)).config_revision
            changed = notifications.read_text(encoding="utf-8")
            first_start = changed.index("  - id: another_notice")
            second_start = changed.index("  - id: example_notice")
            group_start = changed.index("recipient_groups:")
            notifications.write_text(
                changed[:first_start] + changed[second_start:group_start] + changed[first_start:second_start] + changed[group_start:],
                encoding="utf-8",
            )
            self.assertEqual(normalize_bundle(load_bundle(root)).config_revision, first_revision)

            routines = root / "domains" / "routines.yaml"
            routine_text = routines.read_text(encoding="utf-8").replace(
                "    steps: []",
                "    steps:\n"
                "      - id: first_wait\n"
                "        type: wait\n"
                "        label: First wait\n"
                "        duration_seconds: 1\n"
                "        required: true\n"
                "        max_lateness_seconds: 10\n"
                "        on_failure: stop\n"
                "      - id: second_wait\n"
                "        type: wait\n"
                "        label: Second wait\n"
                "        duration_seconds: 2\n"
                "        required: true\n"
                "        max_lateness_seconds: 10\n"
                "        on_failure: stop",
            )
            routines.write_text(routine_text, encoding="utf-8")
            ordered = normalize_bundle(load_bundle(root)).config_revision
            first_step = routine_text.index("      - id: first_wait")
            second_step = routine_text.index("      - id: second_wait")
            routines.write_text(routine_text[:first_step] + routine_text[second_step:] + routine_text[first_step:second_step], encoding="utf-8")
            self.assertNotEqual(normalize_bundle(load_bundle(root)).config_revision, ordered)

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
