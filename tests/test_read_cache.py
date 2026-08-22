from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.read_cache import BoundedReadCache


class ReadCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.cache: BoundedReadCache[dict[str, object]] = BoundedReadCache(clock=lambda: self.now)

    def test_hit_within_ttl_does_not_call_provider_again(self) -> None:
        calls = 0

        def load() -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"value": calls}

        first = self.cache.read("weather", ttl_seconds=30, stale_max_seconds=900, loader=load)
        self.now += 20
        second = self.cache.read("weather", ttl_seconds=30, stale_max_seconds=900, loader=load)

        self.assertEqual(calls, 1)
        self.assertEqual(first.freshness, "fresh")
        self.assertEqual(second.age_seconds, 20)

    def test_expired_entry_refreshes_and_replaces_value(self) -> None:
        values = iter(({"value": 1}, {"value": 2}))
        self.cache.read("forecast", ttl_seconds=60, stale_max_seconds=120, loader=lambda: next(values))
        self.now += 61

        refreshed = self.cache.read(
            "forecast",
            ttl_seconds=60,
            stale_max_seconds=120,
            loader=lambda: next(values),
        )

        self.assertEqual(refreshed.value, {"value": 2})
        self.assertEqual(refreshed.age_seconds, 0)

    def test_provider_error_returns_stale_value_only_inside_bound(self) -> None:
        self.cache.read("calendar", ttl_seconds=60, stale_max_seconds=600, loader=lambda: {"events": [1]})
        self.now += 61

        stale = self.cache.read(
            "calendar",
            ttl_seconds=60,
            stale_max_seconds=600,
            loader=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        )

        self.assertEqual(stale.freshness, "stale")
        self.assertEqual(stale.stale_reason, "provider_refresh_failed")
        self.assertEqual(stale.value, {"events": [1]})

    def test_provider_error_raises_after_stale_bound(self) -> None:
        self.cache.read("news", ttl_seconds=300, stale_max_seconds=1800, loader=lambda: {"headlines": [1]})
        self.now += 1801

        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.cache.read(
                "news",
                ttl_seconds=300,
                stale_max_seconds=1800,
                loader=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            )

    def test_force_refresh_never_hides_provider_failure_when_stale_is_disabled(self) -> None:
        self.cache.read("health", ttl_seconds=60, stale_max_seconds=600, loader=lambda: {"ok": True})

        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.cache.read(
                "health",
                ttl_seconds=60,
                stale_max_seconds=600,
                loader=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                force_refresh=True,
                allow_stale=False,
            )

    def test_values_are_copied_at_cache_boundary(self) -> None:
        first = self.cache.read("copy", ttl_seconds=60, stale_max_seconds=600, loader=lambda: {"items": []})
        first.value["items"].append("mutation")  # type: ignore[union-attr]

        second = self.cache.read("copy", ttl_seconds=60, stale_max_seconds=600, loader=lambda: {"items": ["new"]})

        self.assertEqual(second.value, {"items": []})


if __name__ == "__main__":
    unittest.main()
