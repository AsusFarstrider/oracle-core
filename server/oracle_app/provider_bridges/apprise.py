from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request


class AppriseBridgeError(Exception):
    retryable = False


class AppriseBridgeConfigurationError(AppriseBridgeError):
    pass


class AppriseBridgeHttpError(AppriseBridgeError):
    def __init__(self, *, status_code: int) -> None:
        super().__init__(f"Apprise returned HTTP {status_code}.")
        self.status_code = int(status_code)
        self.retryable = self.status_code >= 500 or self.status_code == 429


class AppriseBridgeUnreachableError(AppriseBridgeError):
    retryable = True


class AppriseBridgeResponseError(AppriseBridgeError):
    retryable = True


class AppriseBridge:
    provider_name = "apprise"

    def check_health(self, *, settings: dict[str, Any]) -> dict[str, Any]:
        return self.check_health_at(
            base_url=str(settings.get("base_url") or ""),
            timeout_seconds=_timeout(settings),
        )

    def check_health_at(self, *, base_url: str, timeout_seconds: int) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        base_url = str(base_url or "").strip().rstrip("/")
        if not base_url:
            return {
                "status": "failed",
                "provider": self.provider_name,
                "configured": False,
                "available": False,
                "detail": "Apprise is missing required configuration.",
                "missing_config_keys": ["ORACLE_APPRISE_URL/apprise_url"],
                "checked_at": checked_at,
            }
        try:
            payload, status_code = self._request_json(
                request.Request(
                    f"{base_url}/status",
                    headers={"Accept": "application/json"},
                    method="GET",
                ),
                timeout_seconds=_bounded_timeout(timeout_seconds),
            )
        except AppriseBridgeError as exc:
            return {
                "status": "failed",
                "provider": self.provider_name,
                "configured": True,
                "available": False,
                "detail": str(exc),
                "http_status": getattr(exc, "status_code", None),
                "missing_config_keys": [],
                "checked_at": checked_at,
            }
        provider_status = payload.get("status")
        details = provider_status.get("details") if isinstance(provider_status, dict) else []
        return {
            "status": "ok",
            "provider": self.provider_name,
            "configured": True,
            "available": True,
            "detail": "Apprise API is reachable.",
            "http_status": status_code,
            "configuration_locked": payload.get("config_lock") is True,
            "attachments_locked": payload.get("attach_lock") is True,
            "provider_details": [str(item) for item in details or [] if str(item).strip()][:5],
            "missing_config_keys": [],
            "checked_at": checked_at,
        }

    def send(
        self,
        *,
        settings: dict[str, Any],
        config_key: str,
        routing_tag: str,
        title: str,
        body: str,
        notification_type: str = "info",
        body_format: str = "text",
    ) -> dict[str, Any]:
        if settings.get("enabled") is not True:
            raise AppriseBridgeConfigurationError("Apprise delivery is disabled.")
        return self.send_to(
            base_url=str(settings.get("base_url") or ""),
            timeout_seconds=_timeout(settings),
            config_key=config_key,
            routing_tag=routing_tag,
            title=title,
            body=body,
            notification_type=notification_type,
            body_format=body_format,
        )

    def send_to(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        config_key: str,
        routing_tag: str,
        title: str,
        body: str,
        notification_type: str = "info",
        body_format: str = "text",
    ) -> dict[str, Any]:
        base_url = str(base_url or "").strip().rstrip("/")
        if not base_url:
            raise AppriseBridgeConfigurationError("Apprise base URL is required.")
        clean_key = _oracle_identifier(config_key, "config_key")
        clean_tag = _oracle_identifier(routing_tag, "routing_tag")
        clean_title = _required(title, "title")
        clean_body = _required(body, "body")
        clean_type = _choice(
            notification_type,
            {"info", "success", "warning", "failure"},
            "notification_type",
        )
        clean_format = _choice(body_format, {"text", "markdown", "html"}, "body_format")
        payload = json.dumps(
            {
                "title": clean_title,
                "body": clean_body,
                "type": clean_type,
                "format": clean_format,
                "tag": clean_tag,
            }
        ).encode("utf-8")
        req = request.Request(
            f"{base_url}/notify/{parse.quote(clean_key, safe='')}",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _response, status_code = self._request_json(
            req,
            timeout_seconds=_bounded_timeout(timeout_seconds),
            allow_empty=True,
        )
        return {
            "status": "accepted",
            "provider": self.provider_name,
            "http_status": status_code,
            "detail": "Apprise accepted the notification request.",
        }

    @staticmethod
    def _request_json(
        req: request.Request,
        *,
        timeout_seconds: int,
        allow_empty: bool = False,
    ) -> tuple[dict[str, Any], int]:
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = int(response.getcode())
                raw = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            raise AppriseBridgeHttpError(status_code=exc.code) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise AppriseBridgeUnreachableError("Apprise is unreachable.") from exc
        if not 200 <= status_code < 300:
            raise AppriseBridgeHttpError(status_code=status_code)
        if not raw.strip() and allow_empty:
            return {}, status_code
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppriseBridgeResponseError("Apprise returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise AppriseBridgeResponseError("Apprise returned an invalid response.")
        return payload, status_code


def _timeout(settings: dict[str, Any]) -> int:
    return _bounded_timeout(int(settings.get("timeout_seconds") or 8))


def _bounded_timeout(value: int) -> int:
    return max(1, min(int(value), 60))


def _required(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise AppriseBridgeConfigurationError(f"{field} is required.")
    return clean


def _choice(value: str, allowed: set[str], field: str) -> str:
    clean = _required(value, field).lower()
    if clean not in allowed:
        raise AppriseBridgeConfigurationError(f"Unsupported {field}: {value!r}.")
    return clean


def _oracle_identifier(value: str, field: str) -> str:
    clean = _required(value, field).lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", clean):
        raise AppriseBridgeConfigurationError(
            f"{field} must be an allowlisted Oracle identifier."
        )
    return clean
