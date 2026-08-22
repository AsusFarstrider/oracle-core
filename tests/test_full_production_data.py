from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile

from oracle_app.configuration import SecretSnapshot
from oracle_app.full_production_data import migrate_copy, tts_cache_impact
from oracle_app.memory.schema import ensure_schema


ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = ROOT / "examples" / "config"
CLOCK = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)


def test_full_production_copy_migrates_canonical_memory_and_retention() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source.sqlite3"
        destination = root / "destination.sqlite3"
        ensure_schema(source)

        report = migrate_copy(
            source,
            CONFIGURATION,
            SecretSnapshot({
                "HOME_ASSISTANT_EVENT_TOKEN": "example-event-token",
                "HOME_ASSISTANT_TOKEN": "example-home-assistant-token",
                "PLEX_TOKEN": "example-plex-token",
            }),
            destination,
            observed_at=CLOCK,
            apply_retention=True,
        )

        assert report["retention"]["blocked"] is False
        assert report["retention_applied"]["changed_count"] == report["retention"]["changed_count"]
        with sqlite3.connect(destination) as connection:
            assert connection.execute("SELECT alert_id FROM memory_alerts").fetchall() == []
            versions = {row[0] for row in connection.execute("SELECT version FROM memory_schema_migrations")}
        assert {"0008_current_state_and_retention", "0009_durable_alerts"}.issubset(versions)


def test_tts_cache_impact_is_read_only_and_exact() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        cache = Path(temporary)
        (cache / "one.wav").write_bytes(b"one")
        (cache / "two.wav").write_bytes(b"two-two")

        report = tts_cache_impact(cache)

        assert report["files"] == 2
        assert report["bytes"] == 10
        assert sorted(path.name for path in cache.iterdir()) == ["one.wav", "two.wav"]
