from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from oracle_app.ui_snapshot_cache import clear_cached_snapshots, get_cached_snapshot


def test_concurrent_snapshot_miss_builds_once_and_returns_copies() -> None:
    clear_cached_snapshots()
    barrier = Barrier(8)
    counter_lock = Lock()
    build_count = 0

    def request_snapshot() -> dict[str, object]:
        barrier.wait()

        def build() -> dict[str, object]:
            nonlocal build_count
            with counter_lock:
                build_count += 1
            time.sleep(0.01)
            return {"cards": [{"title": "Home"}]}

        return get_cached_snapshot("home", ttl_seconds=30.0, builder=build)

    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(executor.map(lambda _index: request_snapshot(), range(8)))

    snapshots[0]["cards"][0]["title"] = "Mutated"
    assert build_count == 1
    assert all(snapshot["cards"][0]["title"] == "Home" for snapshot in snapshots[1:])
