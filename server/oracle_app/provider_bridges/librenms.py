from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib import error, request

from .network_observations import NetworkMonitoringObservation


class LibreNmsBridge:
    provider_name = "librenms"

    def check_health(self, *, settings: dict[str, Any]) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        base_url = str(settings.get("base_url") or "").strip().rstrip("/")
        api_token = str(settings.get("api_token") or "").strip()
        missing_config_keys = [
            str(item)
            for item in settings.get("missing_config_keys") or []
            if str(item).strip()
        ]
        if not base_url:
            missing_config_keys.append("ORACLE_LIBRENMS_URL/librenms_url")
        if not api_token:
            missing_config_keys.append("ORACLE_LIBRENMS_TOKEN/librenms_token")
        missing_config_keys = sorted(set(missing_config_keys))
        if missing_config_keys:
            return {
                "status": "failed",
                "service": "oracle-brain",
                "provider": self.provider_name,
                "configured": False,
                "available": False,
                "degraded": False,
                "detail": "LibreNMS is missing required config.",
                "missing_config_keys": missing_config_keys,
                "checked_at": checked_at,
            }

        timeout_seconds = max(1, int(settings.get("timeout_seconds") or 5))
        result = self._fetch_alerts(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        if result["error"] is not None:
            error_result = result["error"]
            return {
                "status": "failed",
                "service": "oracle-brain",
                "provider": self.provider_name,
                "configured": True,
                "available": False,
                "degraded": False,
                "detail": error_result["detail"],
                "http_status": error_result.get("http_status"),
                "missing_config_keys": [],
                "checked_at": checked_at,
            }

        raw_alerts = self._extract_alerts(result["payload"])
        active_alerts = len(raw_alerts)
        return {
            "status": "ok",
            "service": "oracle-brain",
            "provider": self.provider_name,
            "configured": True,
            "available": True,
            "degraded": active_alerts > 0,
            "detail": "LibreNMS API is reachable.",
            "http_status": result["http_status"],
            "missing_config_keys": [],
            "checked_at": checked_at,
            "active_alert_count": active_alerts,
        }

    def check_typed_health(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return self._check_connection_health(
            base_url=str(base_url).strip().rstrip("/"),
            api_token=str(api_token).strip(),
            timeout_seconds=max(1, int(timeout_seconds)),
        )

    def get_monitoring_status(self, *, settings: dict[str, Any]) -> NetworkMonitoringObservation:
        return NetworkMonitoringObservation.from_dict(self._get_monitoring_status_dict(settings=settings))

    def get_typed_monitoring_status(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> NetworkMonitoringObservation:
        return NetworkMonitoringObservation.from_dict(
            self._get_connection_monitoring_status(
                base_url=str(base_url).strip().rstrip("/"),
                api_token=str(api_token).strip(),
                timeout_seconds=max(1, int(timeout_seconds)),
            )
        )

    def _get_monitoring_status_dict(self, *, settings: dict[str, Any]) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        if not bool(settings.get("enabled", False)):
            return {
                "status": "unknown",
                "checked_at": checked_at,
                "source": self.provider_name,
                "detail": "LibreNMS not configured.",
                "problems": [],
            }

        base_url = str(settings.get("base_url") or "").strip().rstrip("/")
        api_token = str(settings.get("api_token") or "").strip()
        timeout_seconds = max(1, int(settings.get("timeout_seconds") or 5))
        if not base_url or not api_token:
            return {
                "status": "unknown",
                "checked_at": checked_at,
                "source": self.provider_name,
                "detail": "LibreNMS is enabled but incomplete.",
                "problems": [],
            }

        return self._get_connection_monitoring_status(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            max_interface_detail_fetches=max(
                0,
                int(settings.get("max_interface_detail_fetches") or 150),
            ),
        )

    def _get_connection_monitoring_status(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
        max_interface_detail_fetches: int = 150,
    ) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        result = self._fetch_alerts(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        if result["error"] is not None:
            error_result = result["error"]
            return {
                "status": "unknown",
                "checked_at": checked_at,
                "source": self.provider_name,
                "detail": error_result["detail"],
                "problems": [],
            }

        raw_alerts = self._extract_alerts(result["payload"])
        devices_result = self._fetch_devices(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        devices = [] if devices_result["error"] is not None else self._extract_devices(devices_result["payload"])
        services_result = self._fetch_services(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        services = [] if services_result["error"] is not None else self._extract_services(services_result["payload"])
        interfaces_result = self._fetch_interfaces(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        interfaces = [] if interfaces_result["error"] is not None else self._extract_interfaces(interfaces_result["payload"])
        interfaces = self._with_interface_details(
            interfaces,
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            max_detail_fetches=max_interface_detail_fetches,
        )

        problems: list[str] = []
        alerts: list[dict[str, str]] = []
        for alert in raw_alerts[:3]:
            description = self._describe_alert(alert)
            if description:
                problems.append(description)
        for alert in raw_alerts:
            normalized = self._normalize_alert(alert)
            if normalized:
                alerts.append(normalized)
        normalized_devices = []
        for device in devices:
            normalized = self._normalize_device(device)
            if normalized:
                normalized_devices.append(normalized)
        normalized_services = []
        for service in services:
            normalized = self._normalize_service(service)
            if normalized:
                normalized_services.append(normalized)
        normalized_interfaces = []
        for interface in interfaces:
            normalized = self._normalize_interface(interface)
            if normalized:
                normalized_interfaces.append(normalized)

        if raw_alerts:
            detail = f"LibreNMS reports {len(raw_alerts)} active alert(s)."
            status = "degraded"
        else:
            detail = "LibreNMS reports no active alerts."
            status = "healthy"

        return {
            "status": status,
            "checked_at": checked_at,
            "source": self.provider_name,
            "detail": detail,
            "problems": problems,
            "alerts": alerts,
            "alert_count": len(raw_alerts),
            "devices": normalized_devices,
            "device_count": len(devices),
            "devices_error": (devices_result["error"] or {}).get("detail") if devices_result["error"] is not None else "",
            "services": normalized_services,
            "service_count": len(services),
            "services_error": (services_result["error"] or {}).get("detail") if services_result["error"] is not None else "",
            "interfaces": normalized_interfaces,
            "interface_count": len(interfaces),
            "interfaces_error": (interfaces_result["error"] or {}).get("detail") if interfaces_result["error"] is not None else "",
        }

    def _check_connection_health(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        result = self._fetch_alerts(
            base_url=base_url,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        if result["error"] is not None:
            error_result = result["error"]
            return {
                "status": "failed",
                "service": "oracle-brain",
                "provider": self.provider_name,
                "configured": True,
                "available": False,
                "degraded": False,
                "detail": error_result["detail"],
                "http_status": error_result.get("http_status"),
                "missing_config_keys": [],
                "checked_at": checked_at,
            }
        active_alerts = len(self._extract_alerts(result["payload"]))
        return {
            "status": "ok",
            "service": "oracle-brain",
            "provider": self.provider_name,
            "configured": True,
            "available": True,
            "degraded": active_alerts > 0,
            "detail": "LibreNMS API is reachable.",
            "http_status": result["http_status"],
            "missing_config_keys": [],
            "checked_at": checked_at,
            "active_alert_count": active_alerts,
        }

    def _fetch_alerts(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        req = request.Request(f"{base_url}/api/v0/alerts", method="GET")
        req.add_header("X-Auth-Token", api_token)
        req.add_header("Accept", "application/json")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
                return {
                    "payload": payload,
                    "http_status": int(getattr(response, "status", 200) or 200),
                    "error": None,
                }
        except error.HTTPError as exc:
            return {
                "payload": {},
                "http_status": exc.code,
                "error": {
                    "detail": f"LibreNMS returned HTTP {exc.code}.",
                    "http_status": exc.code,
                },
            }
        except error.URLError as exc:
            return {
                "payload": {},
                "http_status": None,
                "error": {"detail": f"LibreNMS is unreachable: {exc.reason}"},
            }
        except (ValueError, TypeError):
            return {
                "payload": {},
                "http_status": None,
                "error": {"detail": "LibreNMS returned invalid JSON."},
            }

    def _fetch_services(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return self._fetch_json(
            url=f"{base_url}/api/v0/services",
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            error_prefix="LibreNMS services",
        )

    def _fetch_devices(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return self._fetch_json(
            url=f"{base_url}/api/v0/devices",
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            error_prefix="LibreNMS devices",
        )

    def _fetch_interfaces(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return self._fetch_json(
            url=f"{base_url}/api/v0/ports",
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            error_prefix="LibreNMS interfaces",
        )

    def _fetch_interface_detail(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
        port_id: str,
    ) -> dict[str, Any]:
        return self._fetch_json(
            url=f"{base_url}/api/v0/ports/{port_id}",
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            error_prefix="LibreNMS interface",
        )

    def _fetch_json(
        self,
        *,
        url: str,
        api_token: str,
        timeout_seconds: int,
        error_prefix: str,
    ) -> dict[str, Any]:
        req = request.Request(url, method="GET")
        req.add_header("X-Auth-Token", api_token)
        req.add_header("Accept", "application/json")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
                return {
                    "payload": payload,
                    "http_status": int(getattr(response, "status", 200) or 200),
                    "error": None,
                }
        except error.HTTPError as exc:
            return {
                "payload": {},
                "http_status": exc.code,
                "error": {
                    "detail": f"{error_prefix} returned HTTP {exc.code}.",
                    "http_status": exc.code,
                },
            }
        except error.URLError as exc:
            return {
                "payload": {},
                "http_status": None,
                "error": {"detail": f"{error_prefix} is unreachable: {exc.reason}"},
            }
        except (ValueError, TypeError):
            return {
                "payload": {},
                "http_status": None,
                "error": {"detail": f"{error_prefix} returned invalid JSON."},
            }

    def _extract_alerts(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        raw_alerts = payload.get("alerts")
        return raw_alerts if isinstance(raw_alerts, list) else []

    def _extract_services(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        return self._flatten_service_rows(payload.get("services"))

    def _extract_devices(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        for key in ("devices", "devices_list", "data"):
            raw_devices = payload.get(key)
            if isinstance(raw_devices, list):
                return [item for item in raw_devices if isinstance(item, dict)]
        return []

    def _extract_interfaces(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        for key in ("ports", "interfaces", "data", "port"):
            raw_interfaces = payload.get(key)
            if isinstance(raw_interfaces, list):
                return [item for item in raw_interfaces if isinstance(item, dict)]
        return []

    def _with_interface_details(
        self,
        interfaces: list[Any],
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: int,
        max_detail_fetches: int,
    ) -> list[Any]:
        detailed: list[Any] = []
        detail_fetches = 0
        for interface in interfaces:
            if not isinstance(interface, dict):
                detailed.append(interface)
                continue
            port_id = str(interface.get("port_id") or "").strip()
            needs_detail = port_id and (
                not str(interface.get("device_id") or "").strip()
                or not str(interface.get("ifOperStatus") or interface.get("if_oper_status") or "").strip()
                or not str(interface.get("ifAdminStatus") or interface.get("if_admin_status") or "").strip()
            )
            if needs_detail and detail_fetches < max_detail_fetches:
                detail_fetches += 1
                detail_result = self._fetch_interface_detail(
                    base_url=base_url,
                    api_token=api_token,
                    timeout_seconds=timeout_seconds,
                    port_id=port_id,
                )
                if detail_result["error"] is None:
                    detail_rows = self._extract_interfaces(detail_result["payload"])
                    if detail_rows:
                        detailed.append({**interface, **detail_rows[0]})
                        continue
            detailed.append(interface)
        return detailed

    def _flatten_service_rows(self, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        rows: list[Any] = []
        for item in value:
            if isinstance(item, list):
                rows.extend(self._flatten_service_rows(item))
            elif isinstance(item, dict):
                rows.append(item)
        return rows

    def _describe_alert(self, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        description = str(
            alert.get("alert")
            or alert.get("title")
            or alert.get("msg")
            or alert.get("name")
            or ""
        ).strip()
        hostname = str(alert.get("hostname") or "").strip()
        severity = str(alert.get("severity") or "").strip().lower()
        if description and hostname and severity:
            return f"{description} on {hostname} is {severity}."
        if description and hostname:
            return f"{description} on {hostname}."
        if description and severity:
            return f"{description} is {severity}."
        return description

    def _normalize_alert(self, alert: Any) -> dict[str, str]:
        if not isinstance(alert, dict):
            return {}
        fields = {
            "description": str(alert.get("alert") or alert.get("title") or alert.get("msg") or alert.get("name") or "").strip(),
            "hostname": str(alert.get("hostname") or "").strip(),
            "severity": str(alert.get("severity") or "").strip().lower(),
            "state": str(alert.get("state") or "").strip().lower(),
            "rule": str(alert.get("rule") or alert.get("rule_id") or "").strip(),
            "device_id": str(alert.get("device_id") or alert.get("device_id_field") or "").strip(),
            "ip": str(alert.get("ip") or alert.get("device_ip") or alert.get("hostname") or "").strip(),
            "service_id": str(alert.get("service_id") or alert.get("service_id_field") or "").strip(),
            "service_name": str(alert.get("service_name") or alert.get("service") or alert.get("service_type") or "").strip(),
        }
        return {key: value for key, value in fields.items() if value}

    def _normalize_service(self, service: Any) -> dict[str, str]:
        if not isinstance(service, dict):
            return {}
        fields = {
            "service_id": str(service.get("service_id") or "").strip(),
            "device_id": str(service.get("device_id") or "").strip(),
            "service_ip": str(service.get("service_ip") or service.get("ip") or "").strip(),
            "service_name": str(service.get("service_name") or service.get("name") or "").strip(),
            "service_desc": str(service.get("service_desc") or service.get("description") or "").strip(),
            "service_type": str(service.get("service_type") or service.get("type") or "").strip(),
            "service_status": str(service.get("service_status") if service.get("service_status") is not None else "").strip(),
            "service_message": str(service.get("service_message") or service.get("message") or "").strip(),
            "service_disabled": str(service.get("service_disabled") or "").strip(),
            "service_ignore": str(service.get("service_ignore") or "").strip(),
            "service_changed": str(service.get("service_changed") or "").strip(),
        }
        return {key: value for key, value in fields.items() if value}

    def _normalize_device(self, device: Any) -> dict[str, str]:
        if not isinstance(device, dict):
            return {}
        fields = {
            "device_id": str(device.get("device_id") or "").strip(),
            "hostname": str(device.get("hostname") or "").strip(),
            "sys_name": str(device.get("sysName") or device.get("sys_name") or "").strip(),
            "display": str(device.get("display") or device.get("displayName") or "").strip(),
            "ip": str(device.get("ip") or device.get("hostname") or "").strip(),
            "hardware": str(device.get("hardware") or "").strip(),
            "os": str(device.get("os") or "").strip(),
            "type": str(device.get("type") or "").strip(),
            "status": str(device.get("status") if device.get("status") is not None else "").strip(),
            "status_reason": str(device.get("status_reason") or "").strip(),
            "location": str(device.get("location") or "").strip(),
        }
        return {key: value for key, value in fields.items() if value}

    def _normalize_interface(self, interface: Any) -> dict[str, str]:
        if not isinstance(interface, dict):
            return {}
        fields = {
            "port_id": str(interface.get("port_id") or interface.get("if_id") or "").strip(),
            "device_id": str(interface.get("device_id") or "").strip(),
            "if_index": str(interface.get("ifIndex") or interface.get("if_index") or "").strip(),
            "if_name": str(interface.get("ifName") or interface.get("if_name") or interface.get("ifName_field") or "").strip(),
            "if_descr": str(interface.get("ifDescr") or interface.get("if_descr") or interface.get("label") or "").strip(),
            "if_alias": str(interface.get("ifAlias") or interface.get("if_alias") or interface.get("description") or "").strip(),
            "if_type": str(interface.get("ifType") or interface.get("if_type") or "").strip(),
            "if_oper_status": str(
                interface.get("ifOperStatus")
                or interface.get("if_oper_status")
                or interface.get("oper_status")
                or ""
            ).strip(),
            "if_admin_status": str(
                interface.get("ifAdminStatus")
                or interface.get("if_admin_status")
                or interface.get("admin_status")
                or ""
            ).strip(),
            "disabled": str(interface.get("disabled") or "").strip(),
            "ignore": str(interface.get("ignore") or "").strip(),
        }
        return {key: value for key, value in fields.items() if value}
