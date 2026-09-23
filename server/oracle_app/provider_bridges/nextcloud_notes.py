from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib import error, request


class NotesBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code


@dataclass(frozen=True)
class ProviderNote:
    provider_id: str
    title: str
    content: str
    etag: str
    modified: int | None = None


class NextcloudNotesBridge:
    provider_name = "nextcloud_notes"

    def negotiate(self, **settings) -> tuple[int, int]:
        payload, headers = self._call(self._url(settings["base_url"], "/ocs/v2.php/cloud/capabilities?format=json"), "GET", **settings)
        try:
            value = json.loads(payload)["ocs"]["data"]["capabilities"]["notes"]["api_version"]
            versions = value if isinstance(value, list) else [value]
            parsed = [tuple(int(part) for part in str(version).split(".")[:2]) for version in versions]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            header = str(headers.get("X-Notes-API-Versions") or "")
            try:
                parsed = [tuple(int(part) for part in item.strip().split(".")[:2]) for item in header.split(",") if item.strip()]
            except ValueError:
                parsed = []
        compatible = [version for version in parsed if version[0] == 1 and version >= (1, 2)]
        if not compatible:
            raise NotesBridgeError("notes_api_unsupported", "Nextcloud Notes API 1.2 or newer is required.")
        return max(compatible)

    def list_notes(self, **settings) -> list[ProviderNote]:
        self.negotiate(**settings)
        payload, _ = self._call(self._api(settings["base_url"], "/notes?exclude=content"), "GET", **settings)
        values = self._json(payload)
        return [self._note(value, include_content=False) for value in values]

    def read_note(self, provider_id: str, **settings) -> ProviderNote:
        self.negotiate(**settings)
        payload, headers = self._call(self._api(settings["base_url"], f"/notes/{provider_id}"), "GET", **settings)
        value = self._json(payload)
        note = self._note(value, include_content=True)
        return ProviderNote(note.provider_id, note.title, note.content, str(headers.get("ETag") or note.etag), note.modified)

    def create_note(self, *, title: str, content: str, **settings) -> ProviderNote:
        self.negotiate(**settings)
        payload, headers = self._call(self._api(settings["base_url"], "/notes"), "POST", data={"title": title, "content": content}, **settings)
        note = self._note(self._json(payload), include_content=True)
        return self.read_note(note.provider_id, **settings)

    def update_note(self, note: ProviderNote, *, title: str | None = None, content: str | None = None, **settings) -> ProviderNote:
        body = {"title": note.title if title is None else title, "content": note.content if content is None else content}
        self._call(self._api(settings["base_url"], f"/notes/{note.provider_id}"), "PUT", data=body, extra_headers={"If-Match": note.etag}, **settings)
        return self.read_note(note.provider_id, **settings)

    def delete_note(self, note: ProviderNote, **settings) -> None:
        self._call(self._api(settings["base_url"], f"/notes/{note.provider_id}"), "DELETE", extra_headers={"If-Match": note.etag}, **settings)
        try:
            self._call(self._api(settings["base_url"], f"/notes/{note.provider_id}"), "GET", **settings)
        except NotesBridgeError as exc:
            if exc.error_code == "notes_not_found":
                return
            raise
        raise NotesBridgeError("notes_verification_failed", "Notes provider still returned the deleted note.")

    def _call(self, url: str, method: str, *, user: str, credential: str, timeout_seconds: int, base_url: str, data: dict | None = None, extra_headers: dict[str, str] | None = None):
        token = base64.b64encode(f"{user}:{credential}".encode()).decode()
        headers = {"Authorization": f"Basic {token}", "Accept": "application/json", "OCS-APIRequest": "true", "User-Agent": "oracle-brain-notes/1.0", **(extra_headers or {})}
        encoded = None
        if data is not None:
            encoded = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=encoded, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read(), response.headers
        except error.HTTPError as exc:
            code = "notes_conflict" if exc.code == 412 else "notes_not_found" if exc.code == 404 else "notes_provider_unavailable"
            raise NotesBridgeError(code, f"Notes provider returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise NotesBridgeError("notes_provider_unavailable", "Notes provider is unavailable.") from exc

    @staticmethod
    def _json(payload: bytes):
        try:
            return json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NotesBridgeError("notes_provider_invalid", "Notes provider returned invalid JSON.") from exc

    @staticmethod
    def _note(value: dict, *, include_content: bool) -> ProviderNote:
        try:
            return ProviderNote(str(value["id"]), str(value.get("title") or "Untitled"), str(value.get("content") or "") if include_content else "", str(value.get("etag") or ""), value.get("modified"))
        except (KeyError, TypeError) as exc:
            raise NotesBridgeError("notes_provider_invalid", "Notes provider returned an invalid note.") from exc

    @staticmethod
    def _url(base_url: str, path: str) -> str:
        return base_url.rstrip("/") + path

    @staticmethod
    def _api(base_url: str, path: str) -> str:
        return base_url.rstrip("/") + "/index.php/apps/notes/api/v1" + path
