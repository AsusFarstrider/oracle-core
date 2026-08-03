from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from oracle_app.runtime_paths import resolve_runtime_paths, validate_standard_storage_settings


REPO_ROOT = Path(__file__).resolve().parents[1]


class StandardRuntimePathTests(unittest.TestCase):
    def test_development_binding_preserves_source_tree_defaults(self) -> None:
        root = Path("/workspace/oracle")
        paths = resolve_runtime_paths({}, development_root=root)

        self.assertFalse(paths.standard_installation)
        self.assertEqual(paths.memory_database, root / "data/oracle-memory.sqlite3")
        self.assertEqual(paths.alerts_state, root / "data/alerts-state.json")
        self.assertEqual(paths.home_assistant_cache, root / "data/home-assistant-cache.json")
        self.assertEqual(paths.facts_cache, root / "data/facts-cache.json")
        self.assertEqual(paths.tts_cache, root / "data/tts-cache")

    def test_standard_binding_confines_every_runtime_path_to_lifecycle_subtrees(self) -> None:
        root = Path("/srv/oracle")
        paths = resolve_runtime_paths(
            {"ORACLE_STANDARD_INSTALLATION": "1"},
            standard_root=root,
        )

        self.assertTrue(paths.standard_installation)
        writable_paths = (
            paths.memory_database,
            paths.provisional_suggestions_database,
            paths.alerts_state,
            paths.home_assistant_cache,
            paths.facts_cache,
            paths.tts_cache,
            paths.local_host_restart_state,
            paths.local_service_restart_state,
            paths.last_suggestions_packet,
            paths.last_suggestions_response,
            paths.tmp,
        )
        permitted = (root / "data", root / "cache", root / "tmp")
        for path in writable_paths:
            self.assertTrue(
                any(path == parent or path.is_relative_to(parent) for parent in permitted),
                path,
            )
        self.assertFalse(any(path.is_relative_to(root / "revisions") for path in writable_paths))

    def test_standard_process_imports_bind_real_consumers_outside_application_revision(self) -> None:
        environment = dict(os.environ)
        environment["ORACLE_STANDARD_INSTALLATION"] = "1"
        environment["PYTHONPATH"] = str(REPO_ROOT / "server")
        code = """
import json
from oracle_app.constants import ALERTS_STATE_PATH, CACHE_PATH, NETWORK_LOCAL_RESTART_STATE_PATH, NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH
from oracle_app.facts_cache import FACTS_CACHE_PATH
from oracle_app.memory.store import DB_PATH, PROVISIONAL_SUGGESTIONS_DB_PATH
from oracle_app.suggestions.storage import LAST_PACKET_PATH, LAST_RESPONSE_PATH
from tts import PREGENERATED_DIR
print(json.dumps([str(path) for path in (
    ALERTS_STATE_PATH, CACHE_PATH, NETWORK_LOCAL_RESTART_STATE_PATH,
    NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH, FACTS_CACHE_PATH, DB_PATH,
    PROVISIONAL_SUGGESTIONS_DB_PATH, LAST_PACKET_PATH, LAST_RESPONSE_PATH,
    PREGENERATED_DIR,
)]))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        paths = [Path(value) for value in json.loads(completed.stdout)]

        self.assertTrue(paths)
        self.assertTrue(all(path.is_relative_to(Path("/srv/oracle")) for path in paths))
        self.assertFalse(
            any(path.is_relative_to(Path("/srv/oracle/revisions")) for path in paths)
        )

    def test_standard_storage_configuration_must_match_managed_data_paths(self) -> None:
        validate_standard_storage_settings(
            "data/oracle-memory.sqlite3",
            "data/alerts-state.json",
        )

        with self.assertRaisesRegex(ValueError, "managed /srv/oracle data paths"):
            validate_standard_storage_settings(
                "/tmp/oracle-memory.sqlite3",
                "data/alerts-state.json",
            )

    def test_standard_unit_redirects_interpreter_library_and_temporary_caches(self) -> None:
        unit = (REPO_ROOT / "scripts/oracle-brain-standard.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("Environment=PYTHONDONTWRITEBYTECODE=1", unit)
        self.assertIn("Environment=PYTHONPYCACHEPREFIX=/srv/oracle/cache/pycache", unit)
        self.assertIn("Environment=XDG_CACHE_HOME=/srv/oracle/cache/xdg", unit)
        self.assertIn("Environment=HF_HOME=/srv/oracle/cache/huggingface", unit)
        self.assertIn("Environment=TMPDIR=/srv/oracle/tmp", unit)


if __name__ == "__main__":
    unittest.main()
