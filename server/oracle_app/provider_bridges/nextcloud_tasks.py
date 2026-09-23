from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import error, request
from urllib.parse import quote, urljoin
from xml.etree import ElementTree

try:
    from icalendar import Calendar, Todo
except ImportError:  # Bridge-specific dependency.
    Calendar = None
    Todo = None


class TasksBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code


@dataclass(frozen=True)
class ProviderTask:
    provider_id: str
    title: str
    completed: bool
    etag: str
    href: str
    recurring: bool = False


class NextcloudTasksBridge:
    provider_name = "nextcloud_tasks"

    def list_items(self, *, base_url: str, user: str, credential: str, calendar_uri: str, timeout_seconds: int) -> list[ProviderTask]:
        self._require_dependency()
        url = self._collection_url(base_url, user, calendar_uri)
        body = b'''<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/></c:comp-filter></c:filter></c:calendar-query>'''
        payload, _ = self._call(url, "REPORT", user, credential, timeout_seconds, data=body, headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise TasksBridgeError("lists_provider_invalid", "Tasks provider returned invalid DAV data.") from exc
        items: list[ProviderTask] = []
        for response_node in root.findall("{DAV:}response"):
            href = response_node.findtext("{DAV:}href") or ""
            etag = response_node.findtext(".//{DAV:}getetag") or ""
            data = response_node.findtext(".//{urn:ietf:params:xml:ns:caldav}calendar-data")
            if not data:
                continue
            try:
                calendar = Calendar.from_ical(data)
                todo = next(iter(calendar.walk("VTODO")))
                provider_id = str(todo.decoded("UID"))
                title = str(todo.decoded("SUMMARY"))
                status = str(todo.get("STATUS") or "NEEDS-ACTION").upper()
            except (StopIteration, TypeError, ValueError, KeyError) as exc:
                raise TasksBridgeError("lists_provider_invalid", "Tasks provider returned an invalid task.") from exc
            items.append(ProviderTask(provider_id, title, status == "COMPLETED", etag.strip(), urljoin(url, href)))
        return items

    def create_list(self, *, base_url: str, user: str, credential: str, calendar_uri: str, display_name: str, timeout_seconds: int) -> None:
        url = self._collection_url(base_url, user, calendar_uri)
        body = f'''<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>{self._xml(display_name)}</d:displayname><c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>'''.encode()
        self._call(url, "MKCALENDAR", user, credential, timeout_seconds, data=body, headers={"Content-Type": "application/xml; charset=utf-8"})
        self._call(url, "PROPFIND", user, credential, timeout_seconds, data=b'<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>', headers={"Depth": "0", "Content-Type": "application/xml"})

    def create_item(self, *, base_url: str, user: str, credential: str, calendar_uri: str, title: str, timeout_seconds: int) -> ProviderTask:
        provider_id = f"oracle-{uuid.uuid4().hex}@oracle"
        href = self._collection_url(base_url, user, calendar_uri) + quote(provider_id + ".ics", safe="")
        payload = self._task_payload(provider_id, title, False)
        self._call(href, "PUT", user, credential, timeout_seconds, data=payload, headers={"If-None-Match": "*", "Content-Type": "text/calendar; charset=utf-8"})
        return self._read_one(href, user, credential, timeout_seconds)

    def update_item(self, item: ProviderTask, *, user: str, credential: str, timeout_seconds: int, title: str | None = None, completed: bool | None = None) -> ProviderTask:
        payload = self._task_payload(item.provider_id, title if title is not None else item.title, completed if completed is not None else item.completed)
        self._call(item.href, "PUT", user, credential, timeout_seconds, data=payload, headers={"If-Match": item.etag, "Content-Type": "text/calendar; charset=utf-8"})
        return self._read_one(item.href, user, credential, timeout_seconds)

    def delete_item(self, item: ProviderTask, *, user: str, credential: str, timeout_seconds: int) -> None:
        self._call(item.href, "DELETE", user, credential, timeout_seconds, headers={"If-Match": item.etag})
        try:
            self._call(item.href, "GET", user, credential, timeout_seconds)
        except TasksBridgeError as exc:
            if exc.error_code == "lists_not_found":
                return
            raise
        raise TasksBridgeError("lists_verification_failed", "Tasks provider still returned the deleted item.")

    def _read_one(self, href: str, user: str, credential: str, timeout_seconds: int) -> ProviderTask:
        payload, headers = self._call(href, "GET", user, credential, timeout_seconds)
        try:
            calendar = Calendar.from_ical(payload)
            todo = next(iter(calendar.walk("VTODO")))
            return ProviderTask(str(todo.decoded("UID")), str(todo.decoded("SUMMARY")), str(todo.get("STATUS") or "").upper() == "COMPLETED", str(headers.get("ETag") or ""), href)
        except (StopIteration, TypeError, ValueError, KeyError) as exc:
            raise TasksBridgeError("lists_provider_invalid", "Tasks provider returned an invalid task.") from exc

    def _task_payload(self, provider_id: str, title: str, completed: bool) -> bytes:
        self._require_dependency()
        calendar = Calendar()
        calendar.add("prodid", "-//Oracle//Lists//EN")
        calendar.add("version", "2.0")
        todo = Todo()
        todo.add("uid", provider_id)
        todo.add("summary", title)
        todo.add("dtstamp", datetime.now(UTC))
        todo.add("status", "COMPLETED" if completed else "NEEDS-ACTION")
        todo.add("percent-complete", 100 if completed else 0)
        if completed:
            todo.add("completed", datetime.now(UTC))
        calendar.add_component(todo)
        return calendar.to_ical()

    def _call(self, url: str, method: str, user: str, credential: str, timeout_seconds: int, *, data: bytes | None = None, headers: dict[str, str] | None = None):
        token = base64.b64encode(f"{user}:{credential}".encode()).decode()
        request_headers = {"Authorization": f"Basic {token}", "User-Agent": "oracle-brain-lists/1.0", **(headers or {})}
        req = request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read(), response.headers
        except error.HTTPError as exc:
            code = "lists_conflict" if exc.code in {409, 412} else "lists_not_found" if exc.code == 404 else "lists_provider_unavailable"
            raise TasksBridgeError(code, f"Tasks provider returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise TasksBridgeError("lists_provider_unavailable", "Tasks provider is unavailable.") from exc

    @staticmethod
    def _collection_url(base_url: str, user: str, calendar_uri: str) -> str:
        return f"{base_url.rstrip('/')}/remote.php/dav/calendars/{quote(user, safe='')}/{quote(calendar_uri, safe='')}/"

    @staticmethod
    def _xml(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def _require_dependency() -> None:
        if Calendar is None or Todo is None:
            raise TasksBridgeError("lists_bridge_dependency_missing", "The Nextcloud Tasks bridge dependency is not installed.")
