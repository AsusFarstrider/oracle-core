from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DependencyProfileTests(unittest.TestCase):
    def test_complete_locks_are_hash_governed(self) -> None:
        for relative in (
            "server/requirements.lock",
            "server/requirements-fast-whisper.lock",
            "satellite/requirements.lock",
            "requirements-test.lock",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("--generate-hashes", text, relative)
            self.assertIn("--hash=sha256:", text, relative)

    def test_production_and_test_dependencies_are_separate(self) -> None:
        production = (ROOT / "server/requirements.lock").read_text(encoding="utf-8").lower()
        test = (ROOT / "requirements-test.lock").read_text(encoding="utf-8").lower()
        self.assertNotIn("pytest==", production)
        self.assertNotIn("numpy==", production)
        self.assertNotIn("pyyaml==", production)
        self.assertIn("pytest==9.0.2", test)
        self.assertIn("numpy==2.4.2", test)
        self.assertIn("pyyaml==6.0.3", test)

    def test_optional_profiles_remain_additive_and_distinct(self) -> None:
        fast_whisper = (ROOT / "server/requirements-fast-whisper.txt").read_text(encoding="utf-8")
        satellite = (ROOT / "satellite/requirements.txt").read_text(encoding="utf-8")
        self.assertIn("-r requirements.txt", fast_whisper)
        self.assertIn("faster-whisper==1.2.1", fast_whisper)
        self.assertIn("openwakeword==0.6.0", satellite)
        self.assertNotIn("faster-whisper", satellite)


if __name__ == "__main__":
    unittest.main()
