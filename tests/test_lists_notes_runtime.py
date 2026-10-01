from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from oracle_app.configuration.lists_notes_runtime_settings import ListsRuntimeSettings, NotesRuntimeSettings, ProviderObjectIdentity
from oracle_app.lists_notes_runtime import CanonicalListsExecution, CanonicalNotesExecution, ListsNotesError
from oracle_app.provider_bridges.nextcloud_notes import ProviderNote
from oracle_app.provider_bridges.nextcloud_tasks import ProviderTask
from oracle_app.provider_bridges.nextcloud_tasks import TasksBridgeError
from oracle_app.memory.schema import ensure_schema
from oracle_app.memory.store import transaction
from oracle_app.provider_bridges.microsoft_todo import MicrosoftTodoBridge


def _lists_settings() -> ListsRuntimeSettings:
    return ListsRuntimeSettings("revision", True, True, True, True, "nextcloud_tasks", "nextcloud_tasks", "https://cloud.invalid", "phil", "TASKS", 8, 30, 120, MappingProxyType({"groceries": ProviderObjectIdentity("groceries", "Groceries", ("shopping",), ("phil",), "groceries")}), "secret")


def _notes_settings() -> NotesRuntimeSettings:
    return NotesRuntimeSettings("revision", True, True, True, True, "nextcloud_notes", "nextcloud_notes", "https://cloud.invalid", "phil", "NOTES", 8, 30, 120, 20000, 20, MappingProxyType({"reference": ProviderObjectIdentity("reference", "Reference", (), ("phil",), "7")}), "secret")


class FakeTasksBridge:
    provider_name = "nextcloud_tasks"

    def __init__(self) -> None:
        self.items = [ProviderTask("native-a", "Milk", False, '"1"', "https://cloud.invalid/a.ics")]
        self.created_lists: list[tuple[str, str]] = []

    def list_items(self, **kwargs):
        return list(self.items)

    def create_list(self, *, calendar_uri, display_name, **kwargs):
        self.created_lists.append((calendar_uri, display_name))

    def create_item(self, *, title, **kwargs):
        item = ProviderTask("native-b", title, False, '"1"', "https://cloud.invalid/b.ics")
        self.items.append(item)
        return item

    def update_item(self, item, *, title=None, completed=None, **kwargs):
        updated = replace(item, title=item.title if title is None else title, completed=item.completed if completed is None else completed, etag='"2"')
        self.items[self.items.index(item)] = updated
        return updated

    def delete_item(self, item, **kwargs):
        self.items.remove(item)


class FakeNotesBridge:
    provider_name = "nextcloud_notes"

    def __init__(self) -> None:
        self.notes = [ProviderNote("7", "Reference", "original", '"1"')]

    def negotiate(self, **kwargs):
        return (1, 4)

    def list_notes(self, **kwargs):
        return [replace(note, content="") for note in self.notes]

    def read_note(self, provider_id, **kwargs):
        return next(note for note in self.notes if note.provider_id == provider_id)

    def create_note(self, *, title, content, **kwargs):
        note = ProviderNote("8", title, content, '"1"')
        self.notes.append(note)
        return note

    def update_note(self, note, *, title=None, content=None, **kwargs):
        updated = replace(note, title=note.title if title is None else title, content=note.content if content is None else content, etag='"2"')
        self.notes[self.notes.index(note)] = updated
        return updated

    def delete_note(self, note, **kwargs):
        self.notes.remove(note)


def test_lists_are_provider_backed_scoped_and_confirm_destructive_work(tmp_path: Path) -> None:
    bridge = FakeTasksBridge()
    execution = CanonicalListsExecution(_lists_settings(), db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    initial = execution.execute("read", list_id="shopping")
    assert initial["items"] == [{"ref": initial["items"][0]["ref"], "title": "Milk", "completed": False}]
    assert "native-a" not in str(initial)

    execution.execute("add", list_id="groceries", title="Eggs")
    execution.execute("complete_all", list_id="groceries")
    assert all(item.completed for item in bridge.items)
    execution.execute("reopen_all", list_id="groceries")
    assert not any(item.completed for item in bridge.items)
    with pytest.raises(ListsNotesError, match="confirmation"):
        execution.execute("delete_all", list_id="groceries")
    result = execution.execute("delete_all", list_id="groceries", confirmed=True)
    assert result["changed_count"] == 2
    assert bridge.items == []


def test_one_list_bulk_failure_reports_verified_partial_progress(tmp_path: Path) -> None:
    class PartlyFailingBridge(FakeTasksBridge):
        def update_item(self, item, **kwargs):
            if item.title == "Eggs":
                raise TasksBridgeError("lists_rate_limited", "Private Graph payload must not escape.")
            return super().update_item(item, **kwargs)

    bridge = PartlyFailingBridge()
    bridge.items.append(ProviderTask("native-b", "Eggs", False, '"1"', "https://cloud.invalid/b.ics"))
    execution = CanonicalListsExecution(_lists_settings(), db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    result = execution.execute("complete_all", list_id="groceries")
    assert result["status"] == "partial_failure"
    assert result["changed_count"] == 1
    assert result["error"] == "lists_rate_limited"
    assert "Private Graph payload" not in str(result)
    assert bridge.items[0].completed and not bridge.items[1].completed


@pytest.mark.parametrize("operation,recurring_completed", [("complete_all", False), ("reopen_all", True), ("delete_all", False)])
def test_graph_bulk_recurring_preflight_refuses_entire_list_without_writes(tmp_path: Path, operation: str, recurring_completed: bool) -> None:
    class RecordingGraphBridge(FakeTasksBridge):
        provider_name = "microsoft_todo"

        def __init__(self) -> None:
            super().__init__()
            self.items.append(ProviderTask("native-b", "Recurring", recurring_completed, '"1"', "https://graph.microsoft.com/task-b", recurring=True))
            self.writes = 0

        def update_item(self, item, **kwargs):
            self.writes += 1
            return super().update_item(item, **kwargs)

        def delete_item(self, item, **kwargs):
            self.writes += 1
            return super().delete_item(item, **kwargs)

    bridge = RecordingGraphBridge()
    settings = replace(_lists_settings(), provider_type="microsoft_todo")
    execution = CanonicalListsExecution(settings, db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    original = list(bridge.items)
    with pytest.raises(ListsNotesError) as raised:
        execution.execute(operation, list_id="groceries", confirmed=True)
    assert raised.value.error_code == "lists_recurring_task_unsupported"
    assert "no items were changed" in raised.value.detail
    assert bridge.writes == 0
    assert bridge.items == original
    assert execution.execute("read", list_id="groceries")["items"][1]["recurring"] is True


@pytest.mark.parametrize("operation", ["complete", "reopen", "edit", "delete"])
def test_graph_individual_recurring_mutation_refused(tmp_path: Path, operation: str) -> None:
    class RecordingGraphBridge(FakeTasksBridge):
        provider_name = "microsoft_todo"

        def update_item(self, item, **kwargs):
            pytest.fail("recurring task reached provider update")

        def delete_item(self, item, **kwargs):
            pytest.fail("recurring task reached provider delete")

    bridge = RecordingGraphBridge()
    bridge.items = [ProviderTask("native-a", "Recurring", operation == "reopen", '"1"', "https://graph.microsoft.com/task-a", recurring=True)]
    execution = CanonicalListsExecution(replace(_lists_settings(), provider_type="microsoft_todo"), db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    with pytest.raises(ListsNotesError) as raised:
        execution.execute(operation, list_id="groceries", lookup_title="Recurring", title="Changed" if operation == "edit" else None, confirmed=True)
    assert raised.value.error_code == "lists_recurring_task_unsupported"


def test_graph_bulk_ignores_recurring_task_not_applicable_to_operation(tmp_path: Path) -> None:
    class RecordingGraphBridge(FakeTasksBridge):
        provider_name = "microsoft_todo"

    bridge = RecordingGraphBridge()
    bridge.items.append(ProviderTask("native-b", "Already complete", True, '"1"', "https://graph.microsoft.com/task-b", recurring=True))
    execution = CanonicalListsExecution(replace(_lists_settings(), provider_type="microsoft_todo"), db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    result = execution.execute("complete_all", list_id="groceries")
    assert result["changed_count"] == 1
    assert bridge.items[1].recurring and bridge.items[1].completed


def test_runtime_created_list_registration_survives_reconstruction_without_content(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    bridge = FakeTasksBridge()
    created = CanonicalListsExecution(_lists_settings(), db_path=path, bridge=bridge).execute("create_list", title="Hardware", user_id="phil")
    reconstructed = CanonicalListsExecution(_lists_settings(), db_path=path, bridge=bridge)
    assert reconstructed.objects()[created["list"]["id"]].display_name == "Hardware"
    assert "Milk" not in path.read_bytes().decode(errors="ignore")


@pytest.mark.parametrize("selector", ["note_ref", "lookup_title"])
def test_runtime_created_note_delete_tombstones_registration_for_ui_and_voice_selection(tmp_path: Path, selector: str) -> None:
    path = tmp_path / "memory.sqlite3"
    bridge = FakeNotesBridge()
    execution = CanonicalNotesExecution(_notes_settings(), db_path=path, bridge=bridge)
    created = execution.execute("create", title="Canary note", content="Temporary content")
    identity = created["note"]["id"]
    values = {selector: created["note"]["ref"] if selector == "note_ref" else "Canary note"}
    assert execution.execute("read", **values)["note"]["id"] == identity
    with pytest.raises(ListsNotesError, match="requires confirmation"):
        execution.execute("delete", **values)
    assert identity in execution.objects()
    execution.execute("delete", confirmed=True, **values)
    reconstructed = CanonicalNotesExecution(_notes_settings(), db_path=path, bridge=bridge)
    assert identity not in reconstructed.objects()
    assert all(note.provider_id != "8" for note in bridge.notes)
    with transaction(path) as conn:
        row = conn.execute("SELECT status, payload_json FROM memory_current_projections WHERE projection_id=?", (f"provider-object:nextcloud_notes:notes:{identity}",)).fetchone()
    assert row["status"] == "deleted"
    assert "Temporary content" not in row["payload_json"]


def test_runtime_registration_cannot_cross_selected_list_providers(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    graph_settings = replace(
        _lists_settings(), provider_id="todo", provider_type="microsoft_todo",
        base_url="https://graph.microsoft.com/v1.0", user="11111111-1111-1111-1111-111111111111",
        credential_secret="TODO_REFRESH", credential="refresh", oauth_tenant="consumers",
        lists=MappingProxyType({}),
    )
    class FakeGraphBridge(FakeTasksBridge):
        provider_name = "microsoft_todo"

        def create_list(self, *, calendar_uri, display_name, **kwargs):
            return "graph-native-id"

    graph = CanonicalListsExecution(graph_settings, db_path=path, bridge=FakeGraphBridge())
    created = graph.execute("create_list", title="Hardware")
    assert graph.objects()[created["list"]["id"]].provider_object_id == "graph-native-id"
    nextcloud = CanonicalListsExecution(_lists_settings(), db_path=path, bridge=FakeTasksBridge())
    assert created["list"]["id"] not in nextcloud.objects()
    nextcloud_created = nextcloud.execute("create_list", title="Hardware")
    assert nextcloud_created["list"]["id"] == created["list"]["id"]
    assert nextcloud.objects()["hardware"].provider_object_id == "oracle-hardware"
    assert graph.objects()["hardware"].provider_object_id == "graph-native-id"
    assert isinstance(CanonicalListsExecution(graph_settings, db_path=path).bridge, MicrosoftTodoBridge)


def test_existing_unscoped_nextcloud_registration_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    ensure_schema(path)
    now = datetime.now(UTC).isoformat()
    payload = json.dumps({"canonical_id": "hardware", "display_name": "Hardware", "aliases": [], "user_ids": [], "provider_object_id": "oracle-hardware"})
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO memory_current_projections
            (projection_id, created_at, updated_at, observed_at, projection_type, source_id, provider, domain, status, correlation_id, payload_json)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'active', NULL, ?)""",
            ("provider-object:lists:hardware", now, now, now, "provider_object_registration", "nextcloud_tasks", "lists", payload),
        )
    assert CanonicalListsExecution(_lists_settings(), db_path=path, bridge=FakeTasksBridge()).objects()["hardware"].provider_object_id == "oracle-hardware"


def test_notes_support_bounded_individual_mutation_and_confirmation(tmp_path: Path) -> None:
    bridge = FakeNotesBridge()
    execution = CanonicalNotesExecution(_notes_settings(), db_path=tmp_path / "memory.sqlite3", bridge=bridge)
    read = execution.execute("read", note_id="reference")
    assert read["note"]["content"] == "original"
    assert read["note"]["ref"] != "7"
    assert "provider_id" not in read["note"]
    execution.execute("append", note_id="reference", content=" plus")
    assert bridge.notes[0].content == "originalplus"
    execution.execute("rename", note_id="reference", title="New Reference")
    assert bridge.notes[0].title == "New Reference"
    with pytest.raises(ListsNotesError, match="confirmation"):
        execution.execute("delete", note_id="reference")
    execution.execute("delete", note_id="reference", confirmed=True)
    assert bridge.notes == []


def test_lists_and_notes_use_independent_caches(tmp_path: Path) -> None:
    lists_bridge = FakeTasksBridge()
    notes_bridge = FakeNotesBridge()
    lists = CanonicalListsExecution(_lists_settings(), db_path=tmp_path / "memory.sqlite3", bridge=lists_bridge)
    notes = CanonicalNotesExecution(_notes_settings(), db_path=tmp_path / "memory.sqlite3", bridge=notes_bridge)
    lists.snapshot()
    notes.snapshot()
    lists.execute("add", list_id="groceries", title="Bread")
    assert notes.snapshot()["notes"][0]["title"] == "Reference"
