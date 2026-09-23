from __future__ import annotations

import io
import json
from xml.etree import ElementTree
from email.message import Message
from urllib import error

import pytest

from oracle_app.provider_bridges.nextcloud_notes import NextcloudNotesBridge, NotesBridgeError, ProviderNote
from oracle_app.provider_bridges.nextcloud_tasks import NextcloudTasksBridge, ProviderTask, TasksBridgeError


SETTINGS = {"base_url": "https://cloud.invalid", "user": "phil", "credential": "secret", "timeout_seconds": 8}


def test_tasks_bridge_normalizes_dav_vtodo_without_exposing_native_payload(monkeypatch) -> None:
    bridge = NextcloudTasksBridge()
    payload = bridge._task_payload("native-1", "Milk", False)  # noqa: SLF001
    xml = f'''<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/remote.php/dav/calendars/phil/groceries/a.ics</d:href><d:propstat><d:prop><d:getetag>"etag-1"</d:getetag><c:calendar-data>{payload.decode()}</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
    def call(*args, **kwargs):
        ElementTree.fromstring(kwargs["data"])
        return xml, {}
    monkeypatch.setattr(bridge, "_call", call)
    items = bridge.list_items(calendar_uri="groceries", **SETTINGS)
    assert items == [ProviderTask("native-1", "Milk", False, '"etag-1"', "https://cloud.invalid/remote.php/dav/calendars/phil/groceries/a.ics")]


def test_tasks_bridge_maps_etag_precondition_to_conflict(monkeypatch) -> None:
    bridge = NextcloudTasksBridge()
    def fail(*args, **kwargs):
        raise error.HTTPError("https://cloud.invalid/a", 412, "precondition", {}, io.BytesIO())
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(TasksBridgeError) as raised:
        bridge._call("https://cloud.invalid/a", "PUT", "phil", "secret", 8)  # noqa: SLF001
    assert raised.value.error_code == "lists_conflict"


def test_notes_bridge_requires_supported_api_and_uses_etag_for_update(monkeypatch) -> None:
    bridge = NextcloudNotesBridge()
    calls = []
    def fake_call(url, method, **kwargs):
        calls.append((url, method, kwargs.get("extra_headers")))
        headers = Message()
        headers["ETag"] = '"new"'
        if "capabilities" in url:
            return json.dumps({"ocs": {"data": {"capabilities": {"notes": {"api_version": ["1.2", "1.4"]}}}}}).encode(), headers
        return json.dumps({"id": 7, "title": "Reference", "content": "updated", "etag": '"new"'}).encode(), headers
    monkeypatch.setattr(bridge, "_call", fake_call)
    updated = bridge.update_note(ProviderNote("7", "Reference", "old", '"old"'), content="updated", **SETTINGS)
    assert updated.content == "updated"
    put = next(call for call in calls if call[1] == "PUT")
    assert put[2] == {"If-Match": '"old"'}


def test_notes_bridge_rejects_incompatible_api(monkeypatch) -> None:
    bridge = NextcloudNotesBridge()
    monkeypatch.setattr(bridge, "_call", lambda *args, **kwargs: (json.dumps({"ocs": {"data": {"capabilities": {"notes": {"api_version": ["1.1"]}}}}}).encode(), {}))
    with pytest.raises(NotesBridgeError) as raised:
        bridge.negotiate(**SETTINGS)
    assert raised.value.error_code == "notes_api_unsupported"
