from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, eq=False)
class NetworkProbeObservation(Mapping[str, Any]):
    status: str
    checked_at: str
    source: str
    detail: str
    problems: tuple[str, ...] = ()
    checks: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NetworkProbeObservation":
        return cls(
            status=str(value.get("status") or "unknown"),
            checked_at=str(value.get("checked_at") or ""),
            source=str(value.get("source") or "probe"),
            detail=str(value.get("detail") or ""),
            problems=tuple(str(item) for item in value.get("problems") or []),
            checks=tuple(copy.deepcopy(item) for item in value.get("checks") or [] if isinstance(item, dict)),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "checked_at": self.checked_at,
            "source": self.source,
            "detail": self.detail,
            "problems": list(self.problems),
        }
        if self.checks:
            result["checks"] = copy.deepcopy(list(self.checks))
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return False


@dataclass(frozen=True, eq=False)
class NetworkMonitoringObservation(Mapping[str, Any]):
    status: str
    checked_at: str
    source: str
    detail: str
    problems: tuple[str, ...] = ()
    alerts: tuple[dict[str, str], ...] = ()
    devices: tuple[dict[str, str], ...] = ()
    services: tuple[dict[str, str], ...] = ()
    interfaces: tuple[dict[str, str], ...] = ()
    alert_count: int | None = None
    device_count: int | None = None
    service_count: int | None = None
    interface_count: int | None = None
    devices_error: str = ""
    services_error: str = ""
    interfaces_error: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NetworkMonitoringObservation":
        def rows(key: str) -> tuple[dict[str, str], ...]:
            return tuple(copy.deepcopy(item) for item in value.get(key) or [] if isinstance(item, dict))

        def optional_count(key: str) -> int | None:
            raw = value.get(key)
            return int(raw) if raw is not None else None

        return cls(
            status=str(value.get("status") or "unknown"),
            checked_at=str(value.get("checked_at") or ""),
            source=str(value.get("source") or "librenms"),
            detail=str(value.get("detail") or ""),
            problems=tuple(str(item) for item in value.get("problems") or []),
            alerts=rows("alerts"),
            devices=rows("devices"),
            services=rows("services"),
            interfaces=rows("interfaces"),
            alert_count=optional_count("alert_count"),
            device_count=optional_count("device_count"),
            service_count=optional_count("service_count"),
            interface_count=optional_count("interface_count"),
            devices_error=str(value.get("devices_error") or ""),
            services_error=str(value.get("services_error") or ""),
            interfaces_error=str(value.get("interfaces_error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "checked_at": self.checked_at,
            "source": self.source,
            "detail": self.detail,
            "problems": list(self.problems),
        }
        groups = (
            ("alerts", self.alerts, "alert_count", self.alert_count, None, None),
            ("devices", self.devices, "device_count", self.device_count, "devices_error", self.devices_error),
            ("services", self.services, "service_count", self.service_count, "services_error", self.services_error),
            ("interfaces", self.interfaces, "interface_count", self.interface_count, "interfaces_error", self.interfaces_error),
        )
        for rows_key, rows, count_key, count, error_key, error_value in groups:
            if count is None:
                continue
            result[rows_key] = copy.deepcopy(list(rows))
            result[count_key] = count
            if error_key is not None:
                result[error_key] = error_value
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return False
