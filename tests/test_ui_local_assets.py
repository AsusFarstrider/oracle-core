from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FONT_ROOT = REPO_ROOT / "house_ui" / "assets" / "fonts"
SUPPORTED_UI_FILES = (
    REPO_ROOT / "house_ui" / "index.html",
    REPO_ROOT / "house_ui" / "app.css",
    REPO_ROOT / "house_ui" / "app.js",
    REPO_ROOT / "satellite_ui" / "index.html",
    REPO_ROOT / "satellite_ui" / "app.css",
    REPO_ROOT / "satellite_ui" / "app.js",
    REPO_ROOT / "ui" / "index.html",
    REPO_ROOT / "ui" / "logs.html",
    REPO_ROOT / "ui" / "trace.html",
    REPO_ROOT / "ui" / "app.css",
    REPO_ROOT / "ui" / "system.css",
    REPO_ROOT / "ui" / "app.js",
    REPO_ROOT / "ui" / "system.js",
)
EXPECTED_HASHES = {
    "instrument-serif-italic-latin.woff2": "5a51946dfffa82972bc98745359c46761515641fda557c25116459a9f83da4a7",
    "instrument-serif-latin.woff2": "5eb09b5ac0e28b67c2f041c8ba6d244604ca0c0980d65912ab2d47fed84ddc31",
    "manrope-latin.woff2": "a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972",
    "material-symbols-outlined-oracle.woff2": "9999650a0e326963065b2e25ac5d87936ca94a98f2ccce3d9bd6a63f721b3b9e",
}


class LocalUiAssetTests(unittest.TestCase):
    def test_supported_ui_has_no_remote_font_or_icon_dependency(self) -> None:
        forbidden = ("fonts.googleapis.com", "fonts.gstatic.com", "@import url(http", "http://", "https://")
        for path in SUPPORTED_UI_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.relative_to(REPO_ROOT), marker=marker):
                    self.assertNotIn(marker, text)

    def test_local_stylesheet_references_only_present_local_assets(self) -> None:
        stylesheet = (FONT_ROOT / "oracle-fonts.css").read_text(encoding="utf-8")
        references = re.findall(r'url\("\./([^"/]+)"\)', stylesheet)
        self.assertEqual(set(references), set(EXPECTED_HASHES))
        for filename in references:
            self.assertTrue((FONT_ROOT / filename).is_file())

    def test_all_ui_shells_load_the_shared_local_stylesheet(self) -> None:
        for path in (item for item in SUPPORTED_UI_FILES if item.suffix == ".html"):
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn('/ui/assets/fonts/oracle-fonts.css?v=1', path.read_text(encoding="utf-8"))

    def test_local_font_assets_match_reviewed_checksums(self) -> None:
        for filename, expected in EXPECTED_HASHES.items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((FONT_ROOT / filename).read_bytes()).hexdigest(), expected)

    def test_material_symbol_inventory_is_sorted_unique_and_nonempty(self) -> None:
        icons = (FONT_ROOT / "material-symbols-icons.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(icons, sorted(set(icons)))
        self.assertGreater(len(icons), 80)
        for required in ("home", "mic", "open_in_new", "play_arrow", "weather_snowy"):
            self.assertIn(required, icons)

    def test_icon_only_climate_controls_keep_accessible_names(self) -> None:
        source = (REPO_ROOT / "house_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('aria-label="${escapeHtml(cooler.label || "Cooler")}"', source)
        self.assertIn('aria-label="${escapeHtml(warmer.label || "Warmer")}"', source)


if __name__ == "__main__":
    unittest.main()
