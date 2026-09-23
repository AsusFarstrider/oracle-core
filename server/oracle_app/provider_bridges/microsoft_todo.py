"""Delegated Microsoft Graph To Do transport; no persistent token cache."""

from __future__ import annotations

import json
import time
from urllib import error, parse, request

from .nextcloud_tasks import ProviderTask, TasksBridgeError


_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SCOPE = "Tasks.ReadWrite offline_access"


class MicrosoftTodoBridge:
    provider_name = "microsoft_todo"

    def __init__(self, *, tenant: str) -> None:
        self.tenant = tenant
        self._access_token: str | None = None
        self._access_expires_at = 0.0

    def list_items(self, *, base_url: str, user: str, credential: str, calendar_uri: str, timeout_seconds: int) -> list[ProviderTask]:
        path = self._list_path(calendar_uri) + "/tasks"
        values = self._pages(path, base_url=base_url, user=user, credential=credential, timeout_seconds=timeout_seconds)
        return [self._task(value, path) for value in values]

    def create_list(self, *, base_url: str, user: str, credential: str, calendar_uri: str, display_name: str, timeout_seconds: int) -> str:
        del calendar_uri  # Graph creates and assigns its own opaque list ID.
        value = self._graph("POST", "/me/todo/lists", base_url=base_url, user=user, credential=credential, timeout_seconds=timeout_seconds, body={"displayName": display_name})
        try:
            list_id = self._identifier(value)
        except TasksBridgeError as exc:
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted list creation without a verifiable ID.") from exc
        try:
            read = self._graph("GET", self._list_path(list_id), base_url=base_url, user=user, credential=credential, timeout_seconds=timeout_seconds)
        except TasksBridgeError as exc:
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted list creation but verification did not complete.") from exc
        if not isinstance(read, dict) or read.get("displayName") != display_name:
            raise TasksBridgeError("lists_verification_failed", "To Do did not verify the created list.")
        return list_id

    def create_item(self, *, base_url: str, user: str, credential: str, calendar_uri: str, title: str, timeout_seconds: int) -> ProviderTask:
        path = self._list_path(calendar_uri) + "/tasks"
        value = self._graph("POST", path, base_url=base_url, user=user, credential=credential, timeout_seconds=timeout_seconds, body={"title": title})
        try:
            item_id = self._identifier(value)
        except TasksBridgeError as exc:
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted task creation without a verifiable ID.") from exc
        try:
            item = self._read_task(path + "/" + parse.quote(item_id, safe=""), user=user, credential=credential, timeout_seconds=timeout_seconds)
        except TasksBridgeError as exc:
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted task creation but verification did not complete.") from exc
        if item.title != title:
            raise TasksBridgeError("lists_verification_failed", "To Do did not verify the created task.")
        return item

    def update_item(self, item: ProviderTask, *, user: str, credential: str, timeout_seconds: int, title: str | None = None, completed: bool | None = None) -> ProviderTask:
        self._require_nonrecurring(item)
        if not item.etag:
            raise TasksBridgeError("lists_conflict", "To Do did not provide a task concurrency token.")
        body: dict[str, object] = {}
        if title is not None:
            body["title"] = title
        if completed is not None:
            body["status"] = "completed" if completed else "notStarted"
        self._graph("PATCH", item.href, base_url=_GRAPH_ROOT, user=user, credential=credential, timeout_seconds=timeout_seconds, body=body, etag=item.etag)
        try:
            read = self._read_task(item.href, user=user, credential=credential, timeout_seconds=timeout_seconds)
        except TasksBridgeError as exc:
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted the task update but verification did not complete.") from exc
        if (title is not None and read.title != title) or (completed is not None and read.completed != completed):
            raise TasksBridgeError("lists_verification_failed", "To Do did not verify the task update.")
        return read

    def delete_item(self, item: ProviderTask, *, user: str, credential: str, timeout_seconds: int) -> None:
        self._require_nonrecurring(item)
        if not item.etag:
            raise TasksBridgeError("lists_conflict", "To Do did not provide a task concurrency token.")
        self._graph("DELETE", item.href, base_url=_GRAPH_ROOT, user=user, credential=credential, timeout_seconds=timeout_seconds, etag=item.etag)
        try:
            self._read_task(item.href, user=user, credential=credential, timeout_seconds=timeout_seconds)
        except TasksBridgeError as exc:
            if exc.error_code == "lists_not_found":
                return
            raise TasksBridgeError("lists_outcome_unknown", "To Do accepted task deletion but verification did not complete.") from exc
        raise TasksBridgeError("lists_verification_failed", "To Do still returned the deleted task.")

    def _read_task(self, path: str, *, user: str, credential: str, timeout_seconds: int) -> ProviderTask:
        value = self._graph("GET", path, base_url=_GRAPH_ROOT, user=user, credential=credential, timeout_seconds=timeout_seconds)
        return self._task(value, parse.urlsplit(path).path.removeprefix("/v1.0").rsplit("/", 1)[0])

    def _pages(self, path: str, *, base_url: str, user: str, credential: str, timeout_seconds: int) -> list[dict]:
        values: list[dict] = []
        next_path: str | None = path
        seen: set[str] = set()
        for _ in range(32):
            if next_path is None:
                return values
            if next_path in seen:
                break
            seen.add(next_path)
            payload = self._graph("GET", next_path, base_url=base_url, user=user, credential=credential, timeout_seconds=timeout_seconds)
            if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                raise TasksBridgeError("lists_provider_invalid", "To Do returned invalid pagination data.")
            for item in payload["value"]:
                if not isinstance(item, dict):
                    raise TasksBridgeError("lists_provider_invalid", "To Do returned an invalid task.")
                values.append(item)
                if len(values) > 2000:
                    raise TasksBridgeError("lists_provider_limit", "To Do exceeded the bounded task read limit.")
            link = payload.get("@odata.nextLink")
            if link is not None and (
                not isinstance(link, str)
                or not self._safe_url(link, path)
                or parse.urlsplit(link).path != "/v1.0" + path
            ):
                raise TasksBridgeError("lists_provider_invalid", "To Do returned an unsafe next page.")
            next_path = link
        raise TasksBridgeError("lists_provider_limit", "To Do exceeded the bounded page limit.")

    def _graph(self, method: str, path: str, *, base_url: str, user: str, credential: str, timeout_seconds: int, body: dict | None = None, etag: str | None = None):
        if base_url != _GRAPH_ROOT:
            raise TasksBridgeError("lists_provider_invalid", "To Do Graph endpoint is not supported.")
        url = path if path.startswith("https://") else _GRAPH_ROOT + path
        if not self._safe_url(url, "/me/todo/lists"):
            raise TasksBridgeError("lists_provider_invalid", "To Do request URL is outside the bounded Graph API.")
        token = self._token(user, credential, timeout_seconds)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if etag:
            headers["If-Match"] = etag
        try:
            payload = self._http(url, method, timeout_seconds, headers, None if body is None else json.dumps(body).encode())
        except TasksBridgeError as exc:
            if method != "GET" and exc.error_code == "lists_provider_unavailable":
                raise TasksBridgeError("lists_outcome_unknown", "To Do write outcome is unknown; reconcile provider state before retrying.") from exc
            raise
        if not payload:
            return None
        try:
            return json.loads(payload)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TasksBridgeError("lists_provider_invalid", "To Do returned invalid JSON.") from exc

    def _token(self, client_id: str, refresh_token: str, timeout_seconds: int) -> str:
        if self._access_token and time.monotonic() < self._access_expires_at:
            return self._access_token
        if not self.tenant or not client_id or not refresh_token:
            raise TasksBridgeError("lists_reauthorization_required", "To Do delegated authorization is incomplete.")
        url = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
        data = parse.urlencode({"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token, "scope": _SCOPE}).encode()
        try:
            payload = self._http(url, "POST", timeout_seconds, {"Content-Type": "application/x-www-form-urlencoded"}, data, token_request=True)
            value = json.loads(payload)
            token = value["access_token"]
            expires = int(value["expires_in"])
            if not isinstance(token, str) or not token or expires < 1:
                raise ValueError("invalid token response")
        except (KeyError, TypeError, ValueError) as exc:
            raise TasksBridgeError("lists_reauthorization_required", "To Do did not return a usable access token.") from exc
        self._access_token = token
        self._access_expires_at = time.monotonic() + max(1, expires - 120)
        # Deliberately discard any replacement refresh token: only canonical
        # secret-generation authority may persist a credential.
        return token

    @staticmethod
    def _http(url: str, method: str, timeout_seconds: int, headers: dict[str, str], data: bytes | None, *, token_request: bool = False) -> bytes:
        req = request.Request(url, data=data, method=method, headers={"User-Agent": "oracle-brain-lists/1.0", **headers})
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise TasksBridgeError("lists_provider_limit", "To Do response exceeded the bounded size limit.")
                return payload
        except error.HTTPError as exc:
            if token_request or exc.code == 401:
                code = "lists_reauthorization_required"
            elif exc.code == 403:
                code = "lists_scope_denied"
            elif exc.code == 429:
                code = "lists_rate_limited"
            elif exc.code in {409, 412}:
                code = "lists_conflict"
            elif exc.code == 404:
                code = "lists_not_found"
            else:
                code = "lists_provider_unavailable"
            raise TasksBridgeError(code, f"To Do provider returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise TasksBridgeError("lists_provider_unavailable", "To Do provider is unavailable.") from exc

    @staticmethod
    def _task(value: object, list_path: str) -> ProviderTask:
        if not isinstance(value, dict):
            raise TasksBridgeError("lists_provider_invalid", "To Do returned an invalid task.")
        try:
            task_id = MicrosoftTodoBridge._identifier(value)
            title = value["title"]
            status = value["status"]
            etag = value.get("@odata.etag") or ""
            recurrence = value.get("recurrence")
            if not isinstance(title, str) or status not in {"notStarted", "inProgress", "completed", "waitingOnOthers", "deferred"} or not isinstance(etag, str):
                raise ValueError("invalid task fields")
            if recurrence is not None and not isinstance(recurrence, dict):
                raise ValueError("invalid recurrence")
        except (KeyError, ValueError) as exc:
            raise TasksBridgeError("lists_provider_invalid", "To Do returned an invalid task.") from exc
        return ProviderTask(task_id, title, status == "completed", etag, _GRAPH_ROOT + list_path + "/" + parse.quote(task_id, safe=""), recurring=recurrence is not None)

    @staticmethod
    def _require_nonrecurring(item: ProviderTask) -> None:
        if item.recurring:
            raise TasksBridgeError("lists_recurring_task_unsupported", "Recurring Microsoft To Do tasks cannot be changed in Stage 8.")

    @staticmethod
    def _identifier(value: object) -> str:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"]:
            raise TasksBridgeError("lists_provider_invalid", "To Do returned an object without an ID.")
        return value["id"]

    @staticmethod
    def _list_path(list_id: str) -> str:
        return "/me/todo/lists/" + parse.quote(list_id, safe="")

    @staticmethod
    def _safe_url(url: str, path_prefix: str) -> bool:
        parsed = parse.urlsplit(url)
        return parsed.scheme == "https" and parsed.netloc == "graph.microsoft.com" and parsed.path.startswith("/v1.0" + path_prefix) and not parsed.fragment
