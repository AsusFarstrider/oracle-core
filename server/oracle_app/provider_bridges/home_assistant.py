from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from oracle_app.conversation import (
    get_home_assistant_conversation_id,
    set_home_assistant_conversation_id,
)


class HomeAssistantBridgeError(Exception):
    pass


class HomeAssistantBridgeHttpError(HomeAssistantBridgeError):
    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class HomeAssistantBridgeUnreachableError(HomeAssistantBridgeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class HomeAssistantBridgeServiceError(HomeAssistantBridgeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class HomeAssistantExecutionResult:
    payload: dict[str, Any]
    verification_failure: dict[str, Any] | None = None
    returned_conversation_id: str | None = None


class HomeAssistantBridge:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float | None = None) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds is not None else None

    def _timeout(self, legacy_default: float) -> float:
        return self.timeout_seconds if self.timeout_seconds is not None else legacy_default

    def execute_command(
        self,
        command_text: str,
        *,
        source: str | None,
        session_id: str | None,
    ) -> HomeAssistantExecutionResult:
        payload = self._post_conversation(
            command_text,
            source=source,
            session_id=session_id,
        )
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return HomeAssistantExecutionResult(payload={"raw": payload})
        if not isinstance(parsed, dict):
            return HomeAssistantExecutionResult(payload={"raw": payload})

        returned_conversation_id = parsed.get("conversation_id")
        return HomeAssistantExecutionResult(
            payload=parsed,
            returned_conversation_id=(
                returned_conversation_id.strip()
                if isinstance(returned_conversation_id, str) and returned_conversation_id.strip()
                else None
            ),
        )

    def commit_conversation_id(
        self,
        conversation_id: str | None,
        *,
        source: str | None,
        session_id: str | None,
    ) -> None:
        if conversation_id:
            set_home_assistant_conversation_id(source, session_id, conversation_id)

    def _post_conversation(
        self,
        command_text: str,
        *,
        source: str | None,
        session_id: str | None,
    ) -> str:
        body_payload: dict[str, Any] = {"text": command_text, "language": "en"}
        conversation_id = get_home_assistant_conversation_id(source, session_id)
        if conversation_id:
            body_payload["conversation_id"] = conversation_id
        req = request.Request(
            f"{self.base_url}/api/conversation/process",
            data=json.dumps(body_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout(10)) as response:
                return response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HomeAssistantBridgeHttpError(status_code=exc.code, detail=detail) from exc
        except error.URLError as exc:
            raise HomeAssistantBridgeUnreachableError(str(exc.reason)) from exc

    def call_service(
        self,
        *,
        service_domain: str,
        service_name: str,
        entity_id: str,
        timeout: float | None = None,
    ) -> None:
        req = request.Request(
            f"{self.base_url}/api/services/{service_domain}/{service_name}",
            data=json.dumps({"entity_id": entity_id}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout if timeout is not None else self._timeout(8)):
                return
        except (error.HTTPError, error.URLError) as exc:
            detail = getattr(exc, "reason", None) or str(exc)
            raise HomeAssistantBridgeServiceError(str(detail)) from exc

    def wait_for_entity_state(
        self,
        entity_id: str,
        expected_state: str,
        *,
        timeout_seconds: float = 3.0,
        poll_seconds: float = 0.35,
    ) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        latest_state: dict[str, Any] | None = None
        while time.time() < deadline:
            latest_state = self.fetch_entity_state(entity_id)
            normalized = str((latest_state or {}).get("state") or "").strip().lower()
            if normalized == str(expected_state or "").strip().lower():
                return latest_state
            time.sleep(poll_seconds)
        return latest_state

    def fetch_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        req = request.Request(
            f"{self.base_url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self._timeout(5)) as response:
                raw_body = response.read().decode("utf-8")
        except (error.HTTPError, error.URLError):
            return None
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

def extract_success_entity_ids(payload: dict[str, Any]) -> list[str]:
    response = payload.get("response") or {}
    if not isinstance(response, dict):
        return []
    data = response.get("data") or {}
    if not isinstance(data, dict):
        return []
    success = data.get("success") or []
    entity_ids: list[str] = []
    if not isinstance(success, list):
        return entity_ids
    for item in success:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("id") or "").strip()
        if entity_id:
            entity_ids.append(entity_id)
    return entity_ids
