from __future__ import annotations

import io
import json
from pathlib import Path
from urllib import error

import pytest

from oracle_app.configuration.domain_models import ListsConfiguration
from oracle_app.configuration.loader import load_bundle
from oracle_app.configuration.secrets import collect_secret_references
from oracle_app.provider_bridges.microsoft_todo import MicrosoftTodoBridge
from oracle_app.provider_bridges.nextcloud_tasks import ProviderTask, TasksBridgeError


SETTINGS = {"base_url": "https://graph.microsoft.com/v1.0", "user": "11111111-1111-1111-1111-111111111111", "credential": "refresh-secret", "timeout_seconds": 8}


def _bridge(monkeypatch) -> MicrosoftTodoBridge:
    bridge = MicrosoftTodoBridge(tenant="consumers")
    monkeypatch.setattr(bridge, "_token", lambda *args: "access-only")
    return bridge


def test_graph_provider_schema_has_one_explicit_selected_provider() -> None:
    role = ListsConfiguration.model_validate({"enabled": True, "provider": "todo", "providers": {"todo": {"type": "microsoft_todo", "tenant": "consumers", "client_id": SETTINGS["user"], "refresh_token_secret": "TODO_REFRESH", "lists": [{"id": "groceries", "display_name": "Groceries", "provider_list_id": "opaque-graph-list"}]}}})
    assert role.providers["todo"].type == "microsoft_todo"
    assert role.providers["todo"].lists[0].provider_list_id == "opaque-graph-list"


def test_graph_example_secret_is_canonical_and_dormant() -> None:
    bundle = load_bundle(Path(__file__).resolve().parents[1] / "examples" / "config")
    matches = [item for item in collect_secret_references(bundle) if item.logical_id == "MICROSOFT_TODO_REFRESH_TOKEN"]
    assert len(matches) == 1
    assert matches[0].file_role == "domains/lists.yaml"
    assert matches[0].path == "providers.microsoft_todo.refresh_token_secret"
    assert matches[0].required is False


def test_graph_pages_are_bounded_and_normalized(monkeypatch) -> None:
    bridge = _bridge(monkeypatch)
    calls = []
    def fake_http(url, method, timeout, headers, data, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return json.dumps({"value": [{"id": "task-1", "title": "Milk", "status": "notStarted", "recurrence": {"pattern": {"type": "daily"}}, "@odata.etag": 'W/"one"'}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/todo/lists/list-1/tasks?$skiptoken=opaque"}).encode()
        return json.dumps({"value": [{"id": "task-2", "title": "Eggs", "status": "completed", "@odata.etag": 'W/"two"'}]}).encode()
    monkeypatch.setattr(bridge, "_http", fake_http)
    items = bridge.list_items(calendar_uri="list-1", **SETTINGS)
    assert [(item.title, item.completed) for item in items] == [("Milk", False), ("Eggs", True)]
    assert [item.recurring for item in items] == [True, False]
    assert all("refresh-secret" not in url for url in calls)


def test_graph_rejects_cross_origin_page(monkeypatch) -> None:
    bridge = _bridge(monkeypatch)
    monkeypatch.setattr(bridge, "_http", lambda *args, **kwargs: json.dumps({"value": [], "@odata.nextLink": "https://attacker.invalid/steal"}).encode())
    with pytest.raises(TasksBridgeError, match="unsafe next page"):
        bridge.list_items(calendar_uri="list-1", **SETTINGS)


def test_graph_update_uses_etag_and_postread(monkeypatch) -> None:
    bridge = _bridge(monkeypatch)
    calls = []
    def fake_http(url, method, timeout, headers, data, **kwargs):
        calls.append((method, headers, data))
        return json.dumps({"id": "task-1", "title": "Bread", "status": "completed", "@odata.etag": 'W/"new"'}).encode()
    monkeypatch.setattr(bridge, "_http", fake_http)
    item = ProviderTask("task-1", "Milk", False, 'W/"old"', "https://graph.microsoft.com/v1.0/me/todo/lists/list-1/tasks/task-1")
    updated = bridge.update_item(item, user=SETTINGS["user"], credential=SETTINGS["credential"], timeout_seconds=8, title="Bread", completed=True)
    assert updated.completed and updated.title == "Bread"
    assert calls[0][0] == "PATCH" and calls[0][1]["If-Match"] == 'W/"old"'
    assert json.loads(calls[0][2]) == {"title": "Bread", "status": "completed"}
    assert calls[1][0] == "GET"


def test_graph_refuses_mutation_without_concurrency_token(monkeypatch) -> None:
    bridge = _bridge(monkeypatch)
    item = ProviderTask("task-1", "Milk", False, "", "https://graph.microsoft.com/v1.0/me/todo/lists/list-1/tasks/task-1")
    with pytest.raises(TasksBridgeError) as raised:
        bridge.update_item(item, user=SETTINGS["user"], credential=SETTINGS["credential"], timeout_seconds=8, completed=True)
    assert raised.value.error_code == "lists_conflict"


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_graph_bridge_refuses_recurring_write_even_if_called_directly(monkeypatch, operation: str) -> None:
    bridge = _bridge(monkeypatch)
    monkeypatch.setattr(bridge, "_http", lambda *args, **kwargs: pytest.fail("recurring task reached Graph"))
    item = ProviderTask("task-1", "Repeat", False, 'W/"one"', "https://graph.microsoft.com/v1.0/me/todo/lists/list-1/tasks/task-1", recurring=True)
    with pytest.raises(TasksBridgeError) as raised:
        if operation == "update":
            bridge.update_item(item, user=SETTINGS["user"], credential=SETTINGS["credential"], timeout_seconds=8, completed=True)
        else:
            bridge.delete_item(item, user=SETTINGS["user"], credential=SETTINGS["credential"], timeout_seconds=8)
    assert raised.value.error_code == "lists_recurring_task_unsupported"


def test_accepted_create_without_postread_proof_is_outcome_unknown(monkeypatch) -> None:
    bridge = _bridge(monkeypatch)
    calls = []
    def fake_http(url, method, timeout, headers, data, **kwargs):
        calls.append(method)
        if method == "POST":
            return b'{"id":"task-1","title":"Milk"}'
        raise TasksBridgeError("lists_provider_unavailable", "Provider temporarily unavailable.")
    monkeypatch.setattr(bridge, "_http", fake_http)
    with pytest.raises(TasksBridgeError) as raised:
        bridge.create_item(calendar_uri="list-1", title="Milk", **SETTINGS)
    assert calls == ["POST", "GET"]
    assert raised.value.error_code == "lists_outcome_unknown"


@pytest.mark.parametrize("http_status,expected", [(401, "lists_reauthorization_required"), (403, "lists_scope_denied"), (412, "lists_conflict"), (429, "lists_rate_limited"), (404, "lists_not_found")])
def test_graph_errors_are_normalized_without_provider_body(http_status, expected, monkeypatch) -> None:
    def failed(req, timeout):
        raise error.HTTPError(req.full_url, http_status, "error", {}, io.BytesIO(b"private provider payload"))
    monkeypatch.setattr("urllib.request.urlopen", failed)
    with pytest.raises(TasksBridgeError) as raised:
        MicrosoftTodoBridge._http("https://graph.microsoft.com/v1.0/me/todo/lists", "GET", 8, {}, None)
    assert raised.value.error_code == expected
    assert "private provider payload" not in str(raised.value)


def test_refresh_token_stays_immutable_and_access_token_is_memory_only(monkeypatch) -> None:
    bridge = MicrosoftTodoBridge(tenant="consumers")
    requests = []
    def fake_http(url, method, timeout, headers, data, **kwargs):
        requests.append((url, data))
        return json.dumps({"access_token": "access-only", "refresh_token": "replacement-not-persisted", "expires_in": 3600}).encode()
    monkeypatch.setattr(bridge, "_http", fake_http)
    assert bridge._token(SETTINGS["user"], SETTINGS["credential"], 8) == "access-only"
    assert bridge._token(SETTINGS["user"], SETTINGS["credential"], 8) == "access-only"
    assert len(requests) == 1
    assert b"refresh-secret" in requests[0][1]
    assert not hasattr(bridge, "refresh_token")


def test_terminal_refresh_failure_requires_reauthorization(monkeypatch) -> None:
    bridge = MicrosoftTodoBridge(tenant="consumers")
    def failed(req, timeout):
        raise error.HTTPError(req.full_url, 400, "invalid_grant", {}, io.BytesIO(b"sensitive"))
    monkeypatch.setattr("urllib.request.urlopen", failed)
    with pytest.raises(TasksBridgeError) as raised:
        bridge._token(SETTINGS["user"], SETTINGS["credential"], 8)
    assert raised.value.error_code == "lists_reauthorization_required"
