from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration.lists_notes_runtime_settings import (
    ListsRuntimeSettings,
    NotesRuntimeSettings,
    ProviderObjectIdentity,
)
from .memory.provider_object_registrations import (
    load_provider_object_registrations,
    remove_provider_object_registration,
    store_provider_object_registration,
)
from .provider_bridges.nextcloud_notes import NextcloudNotesBridge, ProviderNote
from .provider_bridges.nextcloud_tasks import NextcloudTasksBridge, ProviderTask
from .provider_bridges.microsoft_todo import MicrosoftTodoBridge
from .read_cache import BoundedReadCache


class ListsNotesError(RuntimeError):
    def __init__(self, error_code: str, detail: str, *, options: list[str] | None = None) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail
        self.options = options or []


@dataclass(frozen=True)
class CanonicalObject:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    user_ids: tuple[str, ...]
    provider_object_id: str


class CanonicalListsExecution:
    def __init__(self, settings: ListsRuntimeSettings, *, db_path: Path | None = None, bridge: NextcloudTasksBridge | MicrosoftTodoBridge | None = None) -> None:
        self.settings = settings
        self.db_path = db_path
        self.bridge = bridge or (MicrosoftTodoBridge(tenant=settings.oauth_tenant or "") if settings.provider_type == "microsoft_todo" else NextcloudTasksBridge())
        self._cache: BoundedReadCache[list[ProviderTask]] = BoundedReadCache()

    def objects(self) -> dict[str, CanonicalObject]:
        return _objects(self.settings.lists, "lists", self.db_path, provider=self.settings.provider_type)

    def snapshot(self) -> dict[str, Any]:
        values = []
        freshness = "fresh"
        for selected in self.objects().values():
            items, item_freshness = self._items(selected, force=False)
            if item_freshness == "stale":
                freshness = "stale"
            values.append({**_public_object(selected), "items": [self._public_item(selected, item) for item in items]})
        return {"ok": True, "domain": "lists", "lists": values, "freshness": freshness}

    def execute(self, operation: str, *, list_id: str | None = None, item_ref: str | None = None, title: str | None = None, lookup_title: str | None = None, confirmed: bool = False, user_id: str | None = None) -> dict[str, Any]:
        self._require_enabled(write=operation not in {"read", "health"})
        if operation == "create_list":
            name = _bounded(title, "List name", 200)
            canonical_id = _new_canonical_id(name, self.objects())
            proposed_id = "oracle-" + canonical_id
            created_id = self.bridge.create_list(calendar_uri=proposed_id, display_name=name, **self._provider())
            provider_id = created_id or proposed_id
            store_provider_object_registration(domain="lists", canonical_id=canonical_id, display_name=name, aliases=[], user_ids=[user_id] if user_id else [], provider=self.bridge.provider_name, provider_object_id=provider_id, db_path=self.db_path)
            return {"status": "verified_success", "operation": operation, "list": {"id": canonical_id, "display_name": name}}
        selected = self._select_list(list_id)
        if operation == "add":
            created = self.bridge.create_item(calendar_uri=selected.provider_object_id, title=_bounded(title, "Item title", 1000), **self._provider())
            self._cache.invalidate("lists:")
            return {"status": "verified_success", "operation": operation, "list": _public_object(selected), "item": self._public_item(selected, created)}
        items, freshness = self._items(selected, force=operation != "read")
        if operation == "read":
            return {"status": "verified_success", "operation": operation, "list": _public_object(selected), "items": [self._public_item(selected, item) for item in items], "freshness": freshness}
        if operation in {"complete_all", "reopen_all", "delete_all"}:
            applicable = items if operation == "delete_all" else [item for item in items if item.completed != (operation == "complete_all")]
            if self.settings.provider_type == "microsoft_todo" and any(item.recurring for item in applicable):
                raise ListsNotesError("lists_recurring_task_unsupported", "This list contains an applicable recurring Microsoft To Do task. Stage 8 cannot change recurring tasks; no items were changed.")
            if operation == "delete_all" and not confirmed:
                raise ListsNotesError("confirmation_required", "Deleting every item on a list requires confirmation.")
            changed = 0
            for item in items:
                try:
                    if operation == "delete_all":
                        self.bridge.delete_item(item, user=self.settings.user or "", credential=self.settings.credential or "", timeout_seconds=self.settings.timeout_seconds)
                        changed += 1
                    elif item.completed != (operation == "complete_all"):
                        self.bridge.update_item(item, user=self.settings.user or "", credential=self.settings.credential or "", timeout_seconds=self.settings.timeout_seconds, completed=operation == "complete_all")
                        changed += 1
                except Exception as exc:
                    self._cache.invalidate("lists:")
                    code = getattr(exc, "error_code", "lists_provider_unavailable")
                    uncertain = code in {"lists_provider_unavailable", "lists_verification_failed", "lists_outcome_unknown"}
                    return {
                        "status": "partial_failure" if changed else "outcome_unknown" if uncertain else "failed",
                        "operation": operation,
                        "list": _public_object(selected),
                        "changed_count": changed,
                        "failed_item_ref": self._item_ref(selected, item),
                        "error": code,
                    }
            self._cache.invalidate("lists:")
            return {"status": "verified_success", "operation": operation, "list": _public_object(selected), "changed_count": changed}
        item = self._select_item(selected, items, item_ref=item_ref, title=lookup_title or title)
        if self.settings.provider_type == "microsoft_todo" and item.recurring and operation in {"complete", "reopen", "delete", "remove", "edit"}:
            raise ListsNotesError("lists_recurring_task_unsupported", "Recurring Microsoft To Do tasks cannot be changed in Stage 8; no item was changed.")
        if operation in {"delete", "remove"}:
            if not confirmed:
                raise ListsNotesError("confirmation_required", "Deleting a list item requires confirmation.")
            self.bridge.delete_item(item, user=self.settings.user or "", credential=self.settings.credential or "", timeout_seconds=self.settings.timeout_seconds)
            result = {"status": "verified_success", "operation": "delete", "list": _public_object(selected), "item_ref": self._item_ref(selected, item)}
        elif operation in {"complete", "reopen", "edit"}:
            updated = self.bridge.update_item(item, user=self.settings.user or "", credential=self.settings.credential or "", timeout_seconds=self.settings.timeout_seconds, title=_bounded(title, "Item title", 1000) if operation == "edit" else None, completed=True if operation == "complete" else False if operation == "reopen" else None)
            result = {"status": "verified_success", "operation": operation, "list": _public_object(selected), "item": self._public_item(selected, updated)}
        else:
            raise ListsNotesError("lists_operation_unsupported", "That list operation is not supported.")
        self._cache.invalidate("lists:")
        return result

    def health(self) -> dict[str, Any]:
        if not self.settings.enabled:
            return {"status": "disabled", "provider": None}
        try:
            objects = self.objects()
            if objects:
                self._items(next(iter(objects.values())), force=True)
            return {"status": "ok", "provider": self.settings.provider_type, "configured_lists": len(objects)}
        except Exception as exc:
            return {"status": "degraded", "provider": self.settings.provider_type, "error": getattr(exc, "error_code", "lists_provider_unavailable")}

    def _items(self, selected: CanonicalObject, *, force: bool) -> tuple[list[ProviderTask], str]:
        read = self._cache.read(f"lists:{self.settings.config_revision}:{selected.id}", ttl_seconds=self.settings.fresh_seconds, stale_max_seconds=self.settings.stale_if_error_seconds, force_refresh=force, allow_stale=not force, loader=lambda: self.bridge.list_items(calendar_uri=selected.provider_object_id, **self._provider()))
        return read.value, read.freshness

    def _provider(self) -> dict[str, Any]:
        return {"base_url": self.settings.base_url or "", "user": self.settings.user or "", "credential": self.settings.credential or "", "timeout_seconds": self.settings.timeout_seconds}

    def _select_list(self, value: str | None) -> CanonicalObject:
        return _select_object(self.objects(), value, "list")

    def _select_item(self, selected: CanonicalObject, items: list[ProviderTask], *, item_ref: str | None, title: str | None) -> ProviderTask:
        matches = [item for item in items if item_ref and self._item_ref(selected, item) == item_ref]
        if not matches and title:
            matches = [item for item in items if item.title.casefold() == title.strip().casefold()]
        if len(matches) != 1:
            options = [item.title for item in matches or items[:10]]
            code = "item_not_found" if not matches else "item_ambiguous"
            raise ListsNotesError(code, "Choose exactly one list item before changing it.", options=options)
        return matches[0]

    def _item_ref(self, selected: CanonicalObject, item: ProviderTask) -> str:
        return "item_" + hashlib.sha256(f"{selected.id}\0{item.provider_id}".encode()).hexdigest()[:20]

    def _public_item(self, selected: CanonicalObject, item: ProviderTask) -> dict[str, Any]:
        value = {"ref": self._item_ref(selected, item), "title": item.title, "completed": item.completed}
        if item.recurring:
            value["recurring"] = True
        return value

    def _require_enabled(self, *, write: bool) -> None:
        if not self.settings.enabled or (write and not self.settings.write_enabled) or (not write and not self.settings.read_enabled):
            raise ListsNotesError("lists_unavailable", "Lists are not enabled for that operation.")


class CanonicalNotesExecution:
    def __init__(self, settings: NotesRuntimeSettings, *, db_path: Path | None = None, bridge: NextcloudNotesBridge | None = None) -> None:
        self.settings = settings
        self.db_path = db_path
        self.bridge = bridge or NextcloudNotesBridge()
        self._cache: BoundedReadCache[list[ProviderNote]] = BoundedReadCache()

    def objects(self) -> dict[str, CanonicalObject]:
        return _objects(self.settings.notes, "notes", self.db_path, provider=self.settings.provider_type)

    def snapshot(self) -> dict[str, Any]:
        notes, freshness = self._notes(force=False)
        identities = {item.provider_object_id: item.id for item in self.objects().values()}
        return {"ok": True, "domain": "notes", "notes": [self._public_note(note, canonical_id=identities.get(note.provider_id)) for note in notes[: self.settings.max_search_results]], "freshness": freshness}

    def execute(self, operation: str, *, note_id: str | None = None, note_ref: str | None = None, title: str | None = None, lookup_title: str | None = None, content: str | None = None, query: str | None = None, confirmed: bool = False, user_id: str | None = None) -> dict[str, Any]:
        self._require_enabled(write=operation not in {"read", "search", "health"})
        if operation == "create":
            bounded_title = _bounded(title, "Note title", 200)
            bounded_content = _bounded(content or " ", "Note content", self.settings.max_content_characters).strip()
            created = self.bridge.create_note(title=bounded_title, content=bounded_content, **self._provider())
            canonical_id = _new_canonical_id(bounded_title, self.objects())
            store_provider_object_registration(domain="notes", canonical_id=canonical_id, display_name=bounded_title, aliases=[], user_ids=[user_id] if user_id else [], provider=self.bridge.provider_name, provider_object_id=created.provider_id, db_path=self.db_path)
            self._cache.invalidate("notes:")
            return {"status": "verified_success", "operation": operation, "note": self._public_note(created, canonical_id=canonical_id, include_content=True)}
        if operation == "search":
            term = _bounded(query, "Search query", 500).casefold()
            notes, freshness = self._notes(force=False)
            matches = [note for note in notes if term in note.title.casefold()][: self.settings.max_search_results]
            return {"status": "verified_success", "operation": operation, "notes": [self._public_note(note) for note in matches], "freshness": freshness}
        selected_id, note = self._select_note(note_id=note_id, note_ref=note_ref, title=lookup_title or title)
        full = self.bridge.read_note(note.provider_id, **self._provider())
        if operation == "read":
            return {"status": "verified_success", "operation": operation, "note": self._public_note(full, canonical_id=selected_id, include_content=True)}
        if operation == "delete":
            if not confirmed:
                raise ListsNotesError("confirmation_required", "Deleting a note requires confirmation.")
            self.bridge.delete_note(full, **self._provider())
            if selected_id:
                remove_provider_object_registration("notes", selected_id, provider=self.bridge.provider_name, db_path=self.db_path)
            result = {"status": "verified_success", "operation": operation, "note_ref": self._note_ref(full)}
        elif operation in {"append", "replace", "edit", "rename"}:
            if operation == "append":
                new_content = full.content + _bounded(content, "Note content", self.settings.max_content_characters)
                new_title = None
            elif operation in {"replace", "edit"}:
                new_content = _bounded(content, "Note content", self.settings.max_content_characters)
                new_title = None
            else:
                new_content = None
                new_title = _bounded(title, "Note title", 200)
            if new_content is not None and len(new_content) > self.settings.max_content_characters:
                raise ListsNotesError("note_too_large", "The resulting note exceeds the configured content bound.")
            updated = self.bridge.update_note(full, title=new_title, content=new_content, **self._provider())
            result = {"status": "verified_success", "operation": operation, "note": self._public_note(updated, canonical_id=selected_id, include_content=True)}
        else:
            raise ListsNotesError("notes_operation_unsupported", "That note operation is not supported.")
        self._cache.invalidate("notes:")
        return result

    def health(self) -> dict[str, Any]:
        if not self.settings.enabled:
            return {"status": "disabled", "provider": None}
        try:
            version = self.bridge.negotiate(**self._provider())
            return {"status": "ok", "provider": self.settings.provider_type, "api_version": ".".join(map(str, version))}
        except Exception as exc:
            return {"status": "degraded", "provider": self.settings.provider_type, "error": getattr(exc, "error_code", "notes_provider_unavailable")}

    def _notes(self, *, force: bool) -> tuple[list[ProviderNote], str]:
        read = self._cache.read(f"notes:{self.settings.config_revision}", ttl_seconds=self.settings.fresh_seconds, stale_max_seconds=self.settings.stale_if_error_seconds, force_refresh=force, allow_stale=not force, loader=lambda: self.bridge.list_notes(**self._provider()))
        allowed = {item.provider_object_id for item in self.objects().values()}
        return [note for note in read.value if note.provider_id in allowed], read.freshness

    def _select_note(self, *, note_id: str | None, note_ref: str | None, title: str | None) -> tuple[str | None, ProviderNote]:
        objects = self.objects()
        selected_id = None
        provider_id = None
        if note_id:
            selected = _select_object(objects, note_id, "note")
            selected_id, provider_id = selected.id, selected.provider_object_id
        notes, _ = self._notes(force=True)
        matches = [note for note in notes if provider_id and note.provider_id == provider_id]
        if not matches and note_ref:
            matches = [note for note in notes if self._note_ref(note) == note_ref]
        if not matches and title:
            matches = [note for note in notes if note.title.casefold() == title.strip().casefold()]
        if len(matches) != 1:
            raise ListsNotesError("note_not_found" if not matches else "note_ambiguous", "Choose exactly one note before changing it.", options=[note.title for note in matches or notes[:10]])
        return selected_id, matches[0]

    def _provider(self) -> dict[str, Any]:
        return {"base_url": self.settings.base_url or "", "user": self.settings.user or "", "credential": self.settings.credential or "", "timeout_seconds": self.settings.timeout_seconds}

    @staticmethod
    def _note_ref(note: ProviderNote) -> str:
        return "note_" + hashlib.sha256(note.provider_id.encode()).hexdigest()[:20]

    def _public_note(self, note: ProviderNote, *, canonical_id: str | None = None, include_content: bool = False) -> dict[str, Any]:
        value = {"id": canonical_id, "ref": self._note_ref(note), "title": note.title}
        if include_content:
            value["content"] = note.content[: self.settings.max_content_characters]
        return value

    def _require_enabled(self, *, write: bool) -> None:
        if not self.settings.enabled or (write and not self.settings.write_enabled) or (not write and not self.settings.read_enabled):
            raise ListsNotesError("notes_unavailable", "Notes are not enabled for that operation.")


def _objects(configured: dict[str, ProviderObjectIdentity] | Any, domain: str, db_path: Path | None, *, provider: str | None = None) -> dict[str, CanonicalObject]:
    result = {key: CanonicalObject(value.id, value.display_name, value.aliases, value.user_ids, value.provider_object_id) for key, value in configured.items()}
    for value in load_provider_object_registrations(domain, provider=provider, db_path=db_path):
        try:
            item = CanonicalObject(str(value["canonical_id"]), str(value["display_name"]), tuple(str(item) for item in value.get("aliases", [])), tuple(str(item) for item in value.get("user_ids", [])), str(value["provider_object_id"]))
        except (KeyError, TypeError):
            continue
        result[item.id] = item
    return result


def _select_object(objects: dict[str, CanonicalObject], value: str | None, label: str) -> CanonicalObject:
    term = str(value or "").strip().casefold()
    matches = [item for item in objects.values() if term and term in {item.id.casefold(), item.display_name.casefold(), *(alias.casefold() for alias in item.aliases)}]
    if len(matches) != 1:
        raise ListsNotesError(f"{label}_not_found" if not matches else f"{label}_ambiguous", f"Choose exactly one {label} before continuing.", options=[item.display_name for item in matches or objects.values()])
    return matches[0]


def _new_canonical_id(name: str, existing: dict[str, CanonicalObject]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:48] or "item"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _bounded(value: str | None, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ListsNotesError("invalid_argument", f"{label} must contain between 1 and {maximum} characters.")
    return text


def _public_object(value: CanonicalObject) -> dict[str, Any]:
    return {"id": value.id, "display_name": value.display_name}
