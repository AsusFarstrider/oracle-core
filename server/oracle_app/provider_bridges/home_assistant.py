from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from oracle_app.text_normalization import normalize_text
from oracle_app.conversation import (
    get_home_assistant_conversation_id,
    set_home_assistant_conversation_id,
)


_STATE_VERIFICATION_ATTEMPTS = 8
_STATE_VERIFICATION_POLL_SECONDS = 0.5


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

        unavailable_failure = self.detect_failed_success_targets(
            parsed,
            command_text=command_text,
        )
        if unavailable_failure is not None:
            return HomeAssistantExecutionResult(
                payload=parsed,
                verification_failure=unavailable_failure,
            )

        returned_conversation_id = parsed.get("conversation_id")
        if isinstance(returned_conversation_id, str) and returned_conversation_id.strip():
            set_home_assistant_conversation_id(
                source,
                session_id,
                returned_conversation_id.strip(),
            )
        return HomeAssistantExecutionResult(payload=parsed)

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

    def detect_failed_success_targets(
        self,
        payload: dict[str, Any],
        *,
        command_text: str,
    ) -> dict[str, Any] | None:
        entity_ids = extract_success_entity_ids(payload)
        if not entity_ids:
            return None

        unavailable: list[dict[str, str]] = []
        verification_failed: list[dict[str, str]] = []
        for entity_id in entity_ids:
            expected_outcome = expected_target_outcome(command_text, entity_id)
            state = self.fetch_entity_state_with_retry(
                entity_id,
                expected_outcome=expected_outcome,
            )
            if state is None:
                continue
            current_state = str(state.get("state") or "").strip().lower()
            friendly_name = str(state.get("attributes", {}).get("friendly_name") or entity_id).strip()
            if current_state != "unavailable":
                if state_verification_failed(state, current_state=current_state, expected_outcome=expected_outcome):
                    verification_failed.append(
                        {
                            "entity_id": entity_id,
                            "name": friendly_name,
                            "state": current_state,
                            **serialize_expected_outcome(expected_outcome),
                        }
                    )
                continue
            unavailable.append(
                {
                    "entity_id": entity_id,
                    "name": friendly_name,
                }
            )

        if unavailable:
            return {
                "error": "home_assistant_target_unavailable",
                "detail": "Requested Home Assistant target is unavailable.",
                "unavailable_targets": unavailable,
            }
        if verification_failed:
            return {
                "error": "home_assistant_state_verification_failed",
                "detail": "Requested Home Assistant target did not reach the expected state.",
                "verification_failed_targets": verification_failed,
            }
        return None

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

    def fetch_entity_state_with_retry(
        self,
        entity_id: str,
        *,
        expected_outcome: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        latest_state: dict[str, Any] | None = None
        for attempt in range(_STATE_VERIFICATION_ATTEMPTS):
            latest_state = self.fetch_entity_state(entity_id)
            if latest_state is None:
                return None
            current_state = str(latest_state.get("state") or "").strip().lower()
            if current_state == "unavailable":
                return latest_state
            if not state_verification_failed(
                latest_state,
                current_state=current_state,
                expected_outcome=expected_outcome,
            ):
                return latest_state
            if attempt < _STATE_VERIFICATION_ATTEMPTS - 1:
                time.sleep(_STATE_VERIFICATION_POLL_SECONDS)
        return latest_state


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


def expected_target_outcome(command_text: str, entity_id: str) -> dict[str, Any] | None:
    normalized = normalize_text(command_text)
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0].strip().lower()
    if not domain:
        return None

    if normalized.startswith("turn on "):
        if domain in {"light", "switch", "fan", "input_boolean"}:
            return {"kind": "state", "state": "on"}
        return None
    if normalized.startswith("turn off "):
        if domain in {"light", "switch", "fan", "input_boolean"}:
            return {"kind": "state", "state": "off"}
        return None
    if normalized.startswith("unlock "):
        if domain == "lock":
            return {"kind": "state", "state": "unlocked"}
        return None
    if normalized.startswith("lock "):
        if domain == "lock":
            return {"kind": "state", "state": "locked"}
        return None
    if normalized.startswith("open "):
        if domain == "cover":
            return {"kind": "state", "state": "open"}
        return None
    if normalized.startswith("close "):
        if domain == "cover":
            return {"kind": "state", "state": "closed"}
        return None

    if domain == "climate":
        temperature_match = re.search(r"\bto\s+(\d{2})\b", normalized)
        if temperature_match and ("temperature" in normalized or "thermostat" in normalized):
            return {
                "kind": "attribute_numeric",
                "attribute": "temperature",
                "value": float(temperature_match.group(1)),
                "description": f"{temperature_match.group(1)} degrees",
            }

    if domain == "light":
        brightness_match = re.search(r"\bto\s+(\d{1,3})\s+percent brightness\b", normalized)
        if brightness_match:
            percent = max(0, min(100, int(brightness_match.group(1))))
            expected_brightness = round((percent / 100.0) * 255)
            return {
                "kind": "attribute_numeric",
                "attribute": "brightness",
                "value": float(expected_brightness),
                "tolerance": 2.0,
                "description": f"{percent} percent brightness",
            }
    return None


def state_verification_failed(
    state_payload: dict[str, Any],
    *,
    current_state: str,
    expected_outcome: dict[str, Any] | None,
) -> bool:
    if not expected_outcome:
        return False
    kind = str(expected_outcome.get("kind") or "").strip().lower()
    if kind == "state":
        expected_state = str(expected_outcome.get("state") or "").strip().lower()
        return bool(expected_state and current_state and current_state != expected_state)
    if kind == "attribute_numeric":
        attribute = str(expected_outcome.get("attribute") or "").strip()
        if not attribute:
            return False
        attributes = state_payload.get("attributes") or {}
        if not isinstance(attributes, dict):
            return True
        actual_value = attributes.get(attribute)
        try:
            actual_numeric = float(actual_value)
            expected_numeric = float(expected_outcome.get("value"))
        except (TypeError, ValueError):
            return True
        tolerance = float(expected_outcome.get("tolerance") or 0.0)
        return abs(actual_numeric - expected_numeric) > tolerance
    return False


def serialize_expected_outcome(expected_outcome: dict[str, Any] | None) -> dict[str, Any]:
    if not expected_outcome:
        return {}
    kind = str(expected_outcome.get("kind") or "").strip().lower()
    if kind == "state":
        expected_state = str(expected_outcome.get("state") or "").strip().lower()
        if expected_state:
            return {"expected_state": expected_state}
        return {}
    if kind == "attribute_numeric":
        attribute = str(expected_outcome.get("attribute") or "").strip()
        description = str(expected_outcome.get("description") or "").strip()
        serialized: dict[str, Any] = {}
        if attribute:
            serialized["expected_attribute"] = attribute
        if description:
            serialized["expected_description"] = description
        value = expected_outcome.get("value")
        if value is not None:
            serialized["expected_value"] = value
        return serialized
    return {}
