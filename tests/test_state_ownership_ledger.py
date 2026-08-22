from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "docs" / "reference" / "state-ownership-ledger.json"
SCHEMA_PATH = ROOT / "server" / "oracle_app" / "memory" / "schema.py"
SUGGESTIONS_STORAGE_PATH = ROOT / "server" / "oracle_app" / "suggestions" / "storage.py"
RUNTIME_PATHS_PATH = ROOT / "server" / "oracle_app" / "runtime_paths.py"

PROFILE_FIELDS = {
    "lifecycle",
    "create",
    "read",
    "update",
    "clear_prune_recover",
    "clock_retention",
    "concurrency",
    "snapshot",
    "restart",
}
ENTRY_FIELDS = {
    "id",
    "owner",
    "locations",
    "scope",
    "profile",
    "authority_role",
    "durability",
    "consumers",
    "tests",
    "contracts",
    "findings",
}
REQUIRED_STATE_IDS = {
    "interaction.sessions",
    "interaction.conversation",
    "interaction.interim_events",
    "audiobook.active_registry",
    "audiobook.pending_sync",
    "ui.calendar_drafts",
    "ui.snapshots",
    "memory.alerts",
    "memory.alert_transitions",
    "network.status_cache",
    "network.control_guard",
    "network.control_results",
    "network.local_host_restart",
    "network.local_service_restart",
    "network.recovery_previews",
    "cache.provider_reads",
    "cache.facts",
    "cache.home_assistant",
    "cache.tts",
    "memory.suggestion_exchange_current",
    "brain.application_services",
    "satellite.playback_authority",
    "satellite.reply_audio",
    "satellite.command_cache",
    "satellite.native_music_player",
    "satellite.longform_player",
    "satellite.wake_session",
    "satellite.wake_capture_artifacts",
    "configuration.generations",
    "installation.activation_state",
}


def _ledger() -> dict[str, object]:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def _sqlite_tables(path: Path) -> set[str]:
    return set(
        re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+([a-z][a-z0-9_]*)",
            path.read_text(encoding="utf-8"),
            flags=re.IGNORECASE,
        )
    )


def _memory_state_id(table: str) -> str:
    if table.startswith("memory_"):
        return f"memory.{table.removeprefix('memory_')}"
    return f"memory.{table}"


def test_ledger_entries_resolve_every_required_field_and_evidence_path() -> None:
    ledger = _ledger()
    assert ledger["format_version"] == 1
    profiles = ledger["profiles"]
    entries = ledger["entries"]
    assert isinstance(profiles, dict)
    assert isinstance(entries, list)

    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    assert REQUIRED_STATE_IDS <= set(ids)

    for entry in entries:
        assert ENTRY_FIELDS <= set(entry), entry["id"]
        profile = profiles[entry["profile"]]
        resolved = {**profile, **entry}
        assert PROFILE_FIELDS <= set(resolved), entry["id"]
        for field in ENTRY_FIELDS | PROFILE_FIELDS:
            assert resolved[field] not in (None, "", []), (entry["id"], field)
        for field in ("locations", "tests", "contracts"):
            for relative in entry[field]:
                assert (ROOT / relative).exists(), (entry["id"], relative)


def test_all_sixteen_canonical_memory_tables_have_stable_ledger_entries() -> None:
    tables = _sqlite_tables(SCHEMA_PATH) | _sqlite_tables(SUGGESTIONS_STORAGE_PATH)
    assert len(tables) == 16
    ledger_ids = {entry["id"] for entry in _ledger()["entries"]}
    assert {_memory_state_id(table) for table in tables} <= ledger_ids


def test_all_runtime_path_bindings_are_explicitly_reconciled() -> None:
    tree = ast.parse(RUNTIME_PATHS_PATH.read_text(encoding="utf-8"))
    bindings = {
        node.name
        for parent in ast.walk(tree)
        if isinstance(parent, ast.ClassDef) and parent.name == "RuntimePathBindings"
        for node in parent.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(decorator, ast.Name) and decorator.id == "property" for decorator in node.decorator_list)
    }
    ledger = _ledger()
    owners = ledger["runtime_path_owners"]
    assert set(owners) == bindings
    state_ids = {entry["id"] for entry in ledger["entries"]}
    for binding, owner_ids in owners.items():
        assert owner_ids, binding
        assert set(owner_ids) <= state_ids, binding


def test_every_bounded_provider_read_cache_source_is_in_the_ledger() -> None:
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "server" / "oracle_app").rglob("*.py")
        if "BoundedReadCache[" in path.read_text(encoding="utf-8")
    }
    documented = {
        location
        for entry in _ledger()["entries"]
        for location in entry["locations"]
    }
    assert discovered <= documented
