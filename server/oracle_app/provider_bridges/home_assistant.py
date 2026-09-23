from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

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


class HomeAssistantBridge:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float | None = None) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds is not None else None

    def _timeout(self, legacy_default: float) -> float:
        return self.timeout_seconds if self.timeout_seconds is not None else legacy_default

    def call_service(
        self,
        *,
        service_domain: str,
        service_name: str,
        entity_id: str,
        timeout: float | None = None,
    ) -> None:
        self._post_service(
            service_domain=service_domain,
            service_name=service_name,
            data={"entity_id": entity_id},
            timeout=timeout,
        )

    def set_power(self, *, entity_id: str, enabled: bool) -> None:
        domain = self._require_entity_domain(entity_id, {"fan", "light", "switch"})
        self._post_service(
            service_domain=domain,
            service_name="turn_on" if enabled else "turn_off",
            data={"entity_id": entity_id},
        )

    def set_access(self, *, entity_id: str, state: str) -> None:
        operations = {
            ("lock", "locked"): "lock",
            ("lock", "unlocked"): "unlock",
            ("cover", "closed"): "close_cover",
            ("cover", "open"): "open_cover",
            ("alarm_control_panel", "armed"): "alarm_arm_away",
            ("alarm_control_panel", "disarmed"): "alarm_disarm",
        }
        domain = self._require_entity_domain(entity_id, {item[0] for item in operations})
        service = operations.get((domain, state))
        if service is None:
            raise HomeAssistantBridgeServiceError("Unsupported mapped access operation.")
        self._post_service(service_domain=domain, service_name=service, data={"entity_id": entity_id})

    def set_climate_temperature(self, *, entity_id: str, temperature: float) -> None:
        self._require_entity_domain(entity_id, {"climate"})
        self._post_service(
            service_domain="climate",
            service_name="set_temperature",
            data={"entity_id": entity_id, "temperature": temperature},
        )

    def invoke_configured_unit(self, *, entity_id: str) -> None:
        domain = self._require_entity_domain(entity_id, {"automation", "scene", "script"})
        service = "trigger" if domain == "automation" else "turn_on"
        self._post_service(service_domain=domain, service_name=service, data={"entity_id": entity_id})

    @staticmethod
    def _require_entity_domain(entity_id: str, allowed: set[str]) -> str:
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain not in allowed:
            raise HomeAssistantBridgeServiceError("Mapped entity is incompatible with this operation.")
        return domain

    def _post_service(
        self,
        *,
        service_domain: str,
        service_name: str,
        data: dict[str, object],
        timeout: float | None = None,
    ) -> None:
        req = request.Request(
            f"{self.base_url}/api/services/{service_domain}/{service_name}",
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout if timeout is not None else self._timeout(8)):
                return
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
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
        except (error.HTTPError, error.URLError, TimeoutError):
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
