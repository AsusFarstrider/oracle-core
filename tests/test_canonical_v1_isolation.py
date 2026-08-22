from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MODULES = (
    "server/oracle_app/api.py",
    "server/oracle_app/brain_application_composition.py",
    "server/oracle_app/orchestration_recovery.py",
    "server/oracle_app/ui_audio.py",
    "server/oracle_app/ui_audio_control.py",
    "server/oracle_app/ui_calendar.py",
    "server/oracle_app/ui_house.py",
    "server/oracle_app/ui_satellite.py",
    "server/oracle_app/ui_weather.py",
)

CANONICAL_EXECUTION_MODULES = (
    "server/oracle_app/health.py",
    "server/oracle_app/handlers/calendar.py",
    "server/oracle_app/handlers/facts.py",
    "server/oracle_app/handlers/home_assistant.py",
    "server/oracle_app/handlers/news.py",
    "server/oracle_app/handlers/weather.py",
    "server/oracle_app/routing.py",
    "server/oracle_app/wake_arbitration_routes.py",
)


class CanonicalV1IsolationTests(unittest.TestCase):
    def test_canonical_execution_edges_do_not_import_retired_config_getters(self) -> None:
        for relative_path in CANONICAL_EXECUTION_MODULES:
            tree = ast.parse(
                (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                filename=relative_path,
            )
            imported_names = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("config")
                for alias in node.names
            }
            with self.subTest(path=relative_path):
                self.assertFalse(
                    {name for name in imported_names if name.startswith("get_")}
                )

    def test_canonical_modules_do_not_import_private_compatibility(self) -> None:
        for relative_path in CANONICAL_MODULES:
            source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative_path)
            imported_modules = {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            imported_modules.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            with self.subTest(path=relative_path):
                self.assertFalse(
                    any("v1_compatibility" in module for module in imported_modules)
                )

    def test_canonical_composition_has_one_canonical_implementation(self) -> None:
        relative_path = "server/oracle_app/brain_application_composition.py"
        tree = ast.parse(
            (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
            filename=relative_path,
        )
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]

        self.assertEqual(classes, ["CanonicalBrainApplicationComposition"])

    def test_routing_has_no_process_global_capability_registry(self) -> None:
        source = (REPO_ROOT / "server/oracle_app/routing.py").read_text(encoding="utf-8")

        self.assertNotIn("ROUTE_CAPABILITY_REGISTRY", source)
        self.assertNotIn("registry or", source)

    def test_canonical_modules_do_not_export_private_v1_tables(self) -> None:
        forbidden_exports = {
            "LegacyBrainApplicationComposition",
            "ROOM_ENTITY_METADATA",
            "ROUTINE_STATE_CHECKS",
            "UI_ACTION_COMMANDS",
            "UI_ACTION_DEFINITIONS",
        }
        for relative_path in CANONICAL_MODULES:
            tree = ast.parse(
                (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                filename=relative_path,
            )
            assigned_names = {
                target.id
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
            }
            with self.subTest(path=relative_path):
                self.assertFalse(assigned_names & forbidden_exports)


if __name__ == "__main__":
    unittest.main()
