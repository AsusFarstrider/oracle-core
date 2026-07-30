from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync-home-assistant.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_home_assistant_script", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load sync-home-assistant.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncHomeAssistantScriptTests(unittest.TestCase):
    def test_main_uses_shared_home_assistant_settings_and_writes_cache(self) -> None:
        module = load_module()
        states = [
            {
                "entity_id": "light.office_lamp",
                "attributes": {"friendly_name": "Office Lamp"},
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir_str:
            cache_path = Path(temp_dir_str) / "home-assistant-cache.json"
            with patch.object(module, "CACHE_PATH", cache_path):
                with patch.object(
                    module,
                    "get_home_assistant_settings",
                    return_value=("http://env.example:8123", "env-token"),
                ) as mock_settings:
                    with patch.object(module, "fetch_states", return_value=states) as mock_fetch:
                        with patch("builtins.print"):
                            module.main()

            written = json.loads(cache_path.read_text(encoding="utf-8"))

        mock_settings.assert_called_once_with()
        mock_fetch.assert_called_once_with("http://env.example:8123", "env-token")
        self.assertEqual(written["entity_count"], 1)
        self.assertEqual(written["entities"][0]["entity_id"], "light.office_lamp")


if __name__ == "__main__":
    unittest.main()
