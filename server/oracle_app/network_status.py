from __future__ import annotations

from datetime import datetime
from typing import Any


def build_network_status_snapshot(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    probe: dict[str, Any],
    monitoring: dict[str, Any],
    satellite_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now().astimezone().isoformat()
    evidence = _build_evidence(
        inventory=inventory,
        probe=probe,
        monitoring=monitoring,
        satellite_control=satellite_control or {},
        received_at=generated_at,
    )
    monitors = _build_monitors(inventory=inventory, evidence=evidence)
    dependencies = _build_dependencies(inventory=inventory, probe=probe, evidence=evidence, monitors=monitors)
    hosts = _build_hosts(inventory=inventory, monitors=monitors)
    services = _build_services(inventory=inventory, monitors=monitors)
    service_groups = _build_service_groups(inventory=inventory, services=services)
    status = _network_status_from_librenms(monitors=monitors, evidence=evidence)
    severity = _severity_for_status(status)
    freshness = _network_freshness_from_librenms(monitors=monitors, evidence=evidence)
    summary = _summary_for_status(status)

    return {
        "status": status,
        "severity": severity,
        "freshness": freshness,
        "generated_at": generated_at,
        "summary": summary,
        "hosts": hosts,
        "services": services,
        "service_groups": service_groups,
        "power_targets": [
            {
                "id": str(item.get("id") or "").strip(),
                "display_name": str(item.get("display_name") or item.get("id") or "").strip(),
                "host_id": str(item.get("host_id") or "").strip(),
                "enabled": item.get("enabled") is True,
                "capabilities": [str(value) for value in item.get("capabilities") or []],
            }
            for item in inventory.get("power_targets") or []
            if isinstance(item, dict)
        ],
        "dependencies": dependencies,
        "monitors": monitors,
        "evidence": evidence,
        "provider_observations": _build_provider_observations(inventory=inventory, monitoring=monitoring),
    }


def _network_status_from_librenms(*, monitors: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    librenms_monitors = [
        item
        for item in monitors
        if str(item.get("provider") or "").strip().lower() == "librenms"
    ]
    if librenms_monitors:
        return _aggregate_status([str(item.get("status") or "unknown") for item in librenms_monitors])
    return _aggregate_status([str(item.get("status") or "unknown") for item in evidence])


def _network_freshness_from_librenms(*, monitors: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    librenms_monitors = [
        item
        for item in monitors
        if str(item.get("provider") or "").strip().lower() == "librenms"
    ]
    if librenms_monitors:
        return _aggregate_freshness(librenms_monitors)
    return _aggregate_freshness(evidence)


def _build_evidence(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    probe: dict[str, Any],
    monitoring: dict[str, Any],
    satellite_control: dict[str, Any],
    received_at: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    probe_status = _normalize_status(str(probe.get("status") or "unknown"), detail=str(probe.get("detail") or ""))
    probe_observed_at = str(probe.get("checked_at") or "")
    evidence.append(
        _evidence_item(
            evidence_id="probe.internet",
            provider=str(probe.get("source") or "probe"),
            observed_at=probe_observed_at,
            received_at=received_at,
            status=probe_status,
            summary=str(probe.get("detail") or "Direct network probe status is unknown."),
            subject_type="dependency",
            subject_id="internet",
            detail=str(probe.get("detail") or ""),
        )
    )

    raw_checks = probe.get("checks")
    if isinstance(raw_checks, list):
        for index, raw_check in enumerate(raw_checks):
            if not isinstance(raw_check, dict):
                continue
            kind = str(raw_check.get("kind") or f"check_{index}").strip().lower() or f"check_{index}"
            status = _normalize_status(str(raw_check.get("status") or "unknown"), detail=str(raw_check.get("detail") or ""))
            evidence.append(
                _evidence_item(
                    evidence_id=f"probe.{kind}",
                    provider=str(probe.get("source") or "probe"),
                    observed_at=probe_observed_at,
                    received_at=received_at,
                    status=status,
                    summary=str(raw_check.get("detail") or f"{kind} probe status is unknown."),
                    subject_type="dependency",
                    subject_id=f"internet_{kind}",
                    detail=str(raw_check.get("detail") or ""),
                    kind=kind,
                )
            )

    monitoring_status = _normalize_status(
        str(monitoring.get("status") or "unknown"),
        detail=str(monitoring.get("detail") or ""),
    )
    monitoring_observed_at = str(monitoring.get("checked_at") or "")
    evidence.append(
        _evidence_item(
            evidence_id="librenms.monitoring",
            provider=str(monitoring.get("source") or "librenms"),
            observed_at=monitoring_observed_at,
            received_at=received_at,
            status=monitoring_status,
            summary=str(monitoring.get("detail") or "LibreNMS monitoring status is unknown."),
            subject_type="monitor",
            subject_id="librenms_monitoring",
            detail=str(monitoring.get("detail") or ""),
        )
    )

    evidence.extend(_build_librenms_monitor_evidence(inventory=inventory, monitoring=monitoring, received_at=received_at))
    evidence.extend(
        _build_satellite_control_monitor_evidence(
            inventory=inventory,
            satellite_control=satellite_control,
            received_at=received_at,
        )
    )

    for index, problem in enumerate(monitoring.get("problems") or []):
        detail = str(problem or "").strip()
        if not detail:
            continue
        evidence.append(
            _evidence_item(
                evidence_id=f"librenms.problem.{index}",
                provider=str(monitoring.get("source") or "librenms"),
                observed_at=monitoring_observed_at,
                received_at=received_at,
                status="degraded",
                summary=detail,
                subject_type="monitor",
                subject_id="librenms_monitoring",
                detail=detail,
            )
        )

    return evidence


def _build_satellite_control_monitor_evidence(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    satellite_control: dict[str, Any],
    received_at: str,
) -> list[dict[str, Any]]:
    observed_at = str(satellite_control.get("checked_at") or "")
    checks = [item for item in satellite_control.get("checks") or [] if isinstance(item, dict)]
    checks_by_monitor_id = {
        str(item.get("monitor_id") or "").strip(): item
        for item in checks
        if str(item.get("monitor_id") or "").strip()
    }
    evidence: list[dict[str, Any]] = []
    for raw_monitor in inventory.get("monitors") or []:
        if not isinstance(raw_monitor, dict):
            continue
        source = str(raw_monitor.get("source") or "").strip().lower()
        monitor_id = str(raw_monitor.get("id") or "").strip()
        if source != "oracle_satellite_control" or not monitor_id:
            continue
        check = checks_by_monitor_id.get(monitor_id)
        if not isinstance(check, dict):
            continue
        status = _normalize_status(str(check.get("status") or "unknown"), detail=str(check.get("detail") or ""))
        detail = str(check.get("detail") or _summary_for_status(status)).strip()
        evidence.append(
            _evidence_item(
                evidence_id=f"oracle_satellite_control.monitor.{monitor_id}",
                provider="oracle_satellite_control",
                observed_at=observed_at,
                received_at=received_at,
                status=status,
                summary=detail,
                subject_type=str(raw_monitor.get("target_type") or "").strip().lower(),
                subject_id=str(raw_monitor.get("target_id") or "").strip(),
                detail=detail,
                kind=str(raw_monitor.get("kind") or "").strip().lower(),
                provider_reference={
                    "source_id": str(check.get("source_id") or ""),
                },
            )
        )
    return evidence


def _build_librenms_monitor_evidence(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    monitoring: dict[str, Any],
    received_at: str,
) -> list[dict[str, Any]]:
    observed_at = str(monitoring.get("checked_at") or "")
    monitoring_status = _normalize_status(str(monitoring.get("status") or "unknown"), detail=str(monitoring.get("detail") or ""))
    alerts = [item for item in monitoring.get("alerts") or [] if isinstance(item, dict)]
    devices = [item for item in monitoring.get("devices") or [] if isinstance(item, dict)]
    services = [item for item in monitoring.get("services") or [] if isinstance(item, dict)]
    interfaces = [item for item in monitoring.get("interfaces") or [] if isinstance(item, dict)]
    if not alerts:
        alerts = [
            {"description": str(problem)}
            for problem in monitoring.get("problems") or []
            if str(problem).strip()
        ]
    evidence: list[dict[str, Any]] = []
    for raw_monitor in inventory.get("monitors") or []:
        if not isinstance(raw_monitor, dict):
            continue
        source = str(raw_monitor.get("source") or "").strip().lower()
        monitor_id = str(raw_monitor.get("id") or "").strip()
        kind = str(raw_monitor.get("kind") or "").strip().lower()
        if source != "librenms" or not monitor_id:
            continue
        matched_alerts = [alert for alert in alerts if _librenms_alert_matches_monitor(alert, raw_monitor)]
        matched_devices = [device for device in devices if _librenms_device_matches_monitor(device, raw_monitor)]
        matched_services = [service for service in services if _librenms_service_matches_monitor(service, raw_monitor)]
        matched_interfaces = [interface for interface in interfaces if _librenms_interface_matches_monitor(interface, raw_monitor)]
        if kind == "device" and matched_devices:
            device = matched_devices[0]
            status = _normalize_librenms_device_status(device)
            detail = str(device.get("status_reason") or device.get("display") or device.get("hostname") or "").strip()
            summary = detail or f"LibreNMS device {device.get('hostname') or monitor_id} is {status}."
            provider_reference = _librenms_device_reference(device)
        elif kind == "device" and devices:
            continue
        elif kind == "service" and matched_services:
            service = matched_services[0]
            status = _normalize_librenms_service_status(service)
            detail = str(service.get("service_message") or service.get("service_desc") or "").strip()
            summary = detail or f"LibreNMS service {service.get('service_name') or monitor_id} is {status}."
            provider_reference = _librenms_service_reference(service)
        elif kind == "service" and services:
            continue
        elif kind == "interface" and matched_interfaces:
            interface = matched_interfaces[0]
            status = _normalize_librenms_interface_status(interface)
            interface_label = _librenms_interface_label(interface) or monitor_id
            detail = _librenms_interface_detail(interface)
            summary = detail or f"LibreNMS interface {interface_label} is {status}."
            provider_reference = _librenms_interface_reference(interface)
        elif kind == "interface" and interfaces:
            continue
        elif kind == "interface":
            continue
        elif matched_alerts:
            status = "degraded"
            detail = _alert_summary(matched_alerts[0])
            summary = detail or "LibreNMS reports an active alert for this monitor."
            provider_reference = {}
        elif monitoring_status in {"healthy", "degraded"}:
            status = "healthy"
            summary = "LibreNMS reports no active alerts for this monitor."
            detail = summary
            provider_reference = {}
        else:
            status = monitoring_status
            summary = str(monitoring.get("detail") or _summary_for_status(status))
            detail = summary
            provider_reference = {}
        evidence.append(
            _evidence_item(
                evidence_id=f"librenms.monitor.{monitor_id}",
                provider="librenms",
                observed_at=observed_at,
                received_at=received_at,
                status=status,
                summary=summary,
                subject_type=str(raw_monitor.get("target_type") or "").strip().lower(),
                subject_id=str(raw_monitor.get("target_id") or "").strip(),
                detail=detail,
                kind=kind,
                provider_reference=provider_reference,
            )
        )
    return evidence


def _evidence_item(
    *,
    evidence_id: str,
    provider: str,
    observed_at: str,
    received_at: str,
    status: str,
    summary: str,
    subject_type: str,
    subject_id: str,
    detail: str,
    kind: str = "",
    provider_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": evidence_id,
        "provider": provider,
        "observed_at": observed_at,
        "received_at": received_at,
        "status": status,
        "severity": _severity_for_status(status),
        "freshness": "fresh" if observed_at else "unknown",
        "summary": summary,
        "subject_type": subject_type,
        "subject_id": subject_id,
    }
    if detail:
        item["detail"] = detail
    if kind:
        item["kind"] = kind
    if provider_reference:
        item["provider_reference"] = provider_reference
    return item


def _build_monitors(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    monitors: list[dict[str, Any]] = []
    for raw in inventory.get("monitors") or []:
        if not isinstance(raw, dict):
            continue
        monitor_id = str(raw.get("id") or "").strip()
        if not monitor_id:
            continue
        evidence_ids = _matching_evidence_ids(raw, evidence)
        monitor_evidence = [item for item in evidence if item["id"] in evidence_ids]
        status = _aggregate_status([str(item.get("status") or "unknown") for item in monitor_evidence])
        monitors.append(
            {
                "id": monitor_id,
                "display_name": str(raw.get("display_name") or monitor_id).strip() or monitor_id,
                "provider": str(raw.get("source") or "unknown").strip().lower() or "unknown",
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness(monitor_evidence),
                "summary": _summary_for_status(status),
                "evidence_ids": evidence_ids,
                "target_type": str(raw.get("target_type") or "").strip().lower(),
                "target_id": str(raw.get("target_id") or "").strip(),
                "kind": str(raw.get("kind") or "").strip().lower(),
            }
        )

    if not any(item.get("id") == "librenms_monitoring" for item in monitors):
        librenms_evidence_ids = [item["id"] for item in evidence if str(item.get("provider")) == "librenms"]
        status = _aggregate_status([str(item.get("status") or "unknown") for item in evidence if item["id"] in librenms_evidence_ids])
        monitors.append(
            {
                "id": "librenms_monitoring",
                "display_name": "LibreNMS Monitoring",
                "provider": "librenms",
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness([item for item in evidence if item["id"] in librenms_evidence_ids]),
                "summary": _summary_for_status(status),
                "evidence_ids": librenms_evidence_ids,
            }
        )
    return monitors


def _matching_evidence_ids(raw_monitor: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    source = str(raw_monitor.get("source") or "").strip().lower()
    kind = str(raw_monitor.get("kind") or "").strip().lower()
    target_type = str(raw_monitor.get("target_type") or "").strip().lower()
    target_id = str(raw_monitor.get("target_id") or "").strip()
    monitor_id = str(raw_monitor.get("id") or "").strip()
    monitor_specific_prefix = "oracle_satellite_control.monitor" if source == "oracle_satellite_control" else "librenms.monitor"
    monitor_specific_id = f"{monitor_specific_prefix}.{monitor_id}" if monitor_id else ""
    if monitor_specific_id and any(str(item.get("id") or "") == monitor_specific_id for item in evidence):
        return [monitor_specific_id]
    matches: list[str] = []
    for item in evidence:
        provider = str(item.get("provider") or "").strip().lower()
        item_kind = str(item.get("kind") or "").strip().lower()
        if source and source not in {provider, "direct_probe" if provider == "probe" else provider}:
            continue
        if source in {"probe", "direct_probe"} and kind and kind != item_kind:
            continue
        if source not in {"probe", "direct_probe"} and kind and item_kind and kind != item_kind:
            continue
        if target_type and target_id and item.get("subject_type") == target_type and item.get("subject_id") == target_id:
            matches.append(str(item["id"]))
            continue
        if item.get("subject_type") == "monitor" and item.get("subject_id") == str(raw_monitor.get("id") or "").strip():
            matches.append(str(item["id"]))
            continue
        if str(item.get("id") or "") == f"librenms.monitor.{str(raw_monitor.get('id') or '').strip()}":
            matches.append(str(item["id"]))
            continue
        if provider == "probe" and source in {"probe", "direct_probe"}:
            matches.append(str(item["id"]))
    return matches


def _librenms_alert_matches_monitor(alert: dict[str, Any], monitor: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value).lower()
        for value in (
            alert.get("description"),
            alert.get("hostname"),
            alert.get("ip"),
            alert.get("severity"),
            alert.get("state"),
            alert.get("rule"),
            alert.get("device_id"),
            alert.get("service_id"),
            alert.get("service_name"),
        )
        if str(value or "").strip()
    )
    if not haystack:
        return False
    match = monitor.get("match") if isinstance(monitor.get("match"), dict) else {}
    candidates = [
        monitor.get("id"),
        monitor.get("display_name"),
        match.get("hostname"),
        match.get("ip"),
        match.get("service_name"),
        match.get("device_id"),
        match.get("service_id"),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip().lower()
        if text and text in haystack:
            return True
    return False


def _librenms_service_matches_monitor(service: dict[str, Any], monitor: dict[str, Any]) -> bool:
    match = monitor.get("match") if isinstance(monitor.get("match"), dict) else {}
    checks: list[bool] = []
    for match_key, service_keys in (
        ("service_id", ("service_id",)),
        ("device_id", ("device_id",)),
        ("ip", ("service_ip", "ip")),
        ("service_name", ("service_name", "service_desc", "service_type")),
    ):
        expected = str(match.get(match_key) or "").strip()
        if not expected:
            continue
        actual_values = [str(service.get(key) or "").strip() for key in service_keys if str(service.get(key) or "").strip()]
        if match_key == "service_name":
            expected_text = _compact_match_text(expected)
            checks.append(any(_compact_match_text(value) == expected_text for value in actual_values))
        else:
            checks.append(any(value == expected for value in actual_values))
    if checks:
        return all(checks)

    candidates = [monitor.get("id"), monitor.get("display_name")]
    service_values = [service.get("service_name"), service.get("service_desc")]
    service_text = {_compact_match_text(value) for value in service_values if str(value or "").strip()}
    return any(_compact_match_text(candidate) in service_text for candidate in candidates if str(candidate or "").strip())


def _librenms_device_matches_monitor(device: dict[str, Any], monitor: dict[str, Any]) -> bool:
    match = monitor.get("match") if isinstance(monitor.get("match"), dict) else {}
    checks: list[bool] = []
    for match_key, device_keys in (
        ("device_id", ("device_id",)),
        ("ip", ("ip", "hostname")),
        ("hostname", ("hostname", "sys_name", "display")),
    ):
        expected = str(match.get(match_key) or "").strip()
        if not expected:
            continue
        actual_values = [str(device.get(key) or "").strip() for key in device_keys if str(device.get(key) or "").strip()]
        if match_key == "hostname":
            expected_text = _compact_match_text(expected)
            checks.append(any(_compact_match_text(value) == expected_text for value in actual_values))
        else:
            checks.append(any(value == expected for value in actual_values))
    if checks:
        return all(checks)

    candidates = [monitor.get("id"), monitor.get("display_name")]
    device_values = [device.get("hostname"), device.get("sys_name"), device.get("display")]
    device_text = {_compact_match_text(value) for value in device_values if str(value or "").strip()}
    return any(_compact_match_text(candidate) in device_text for candidate in candidates if str(candidate or "").strip())


def _librenms_interface_matches_monitor(interface: dict[str, Any], monitor: dict[str, Any]) -> bool:
    match = monitor.get("match") if isinstance(monitor.get("match"), dict) else {}
    checks: list[bool] = []
    for match_key, interface_keys in (
        ("port_id", ("port_id",)),
        ("device_id", ("device_id",)),
        ("if_index", ("if_index",)),
        ("if_name", ("if_name",)),
        ("if_descr", ("if_descr",)),
        ("if_alias", ("if_alias",)),
    ):
        expected = str(match.get(match_key) or "").strip()
        if not expected:
            continue
        actual_values = [
            str(interface.get(key) or "").strip()
            for key in interface_keys
            if str(interface.get(key) or "").strip()
        ]
        if match_key in {"if_name", "if_descr", "if_alias"}:
            expected_text = _compact_match_text(expected)
            checks.append(any(_compact_match_text(value) == expected_text for value in actual_values))
        else:
            checks.append(any(value == expected for value in actual_values))
    if checks:
        return all(checks)

    candidates = [monitor.get("id"), monitor.get("display_name")]
    interface_values = [interface.get("if_name"), interface.get("if_descr"), interface.get("if_alias")]
    interface_text = {_compact_match_text(value) for value in interface_values if str(value or "").strip()}
    return any(_compact_match_text(candidate) in interface_text for candidate in candidates if str(candidate or "").strip())


def _normalize_librenms_device_status(device: dict[str, Any]) -> str:
    raw_status = str(device.get("status") or "").strip().lower()
    if raw_status in {"1", "true", "up", "ok", "healthy"}:
        return "healthy"
    if raw_status in {"0", "false", "down", "failed", "critical"}:
        return "down"
    if raw_status in {"2", "warning", "degraded"}:
        return "degraded"
    if raw_status in {"3", "unknown"}:
        return "unknown"
    return _normalize_status(raw_status)


def _normalize_librenms_interface_status(interface: dict[str, Any]) -> str:
    if str(interface.get("disabled") or "").strip().lower() in {"1", "true", "yes"}:
        return "unconfigured"
    if str(interface.get("ignore") or "").strip().lower() in {"1", "true", "yes"}:
        return "unconfigured"
    admin_status = str(interface.get("if_admin_status") or "").strip().lower()
    oper_status = str(interface.get("if_oper_status") or "").strip().lower()
    if admin_status in {"down", "2", "false"}:
        return "down"
    if oper_status in {"up", "1", "ok", "healthy"}:
        return "healthy"
    if oper_status in {"down", "2", "lowerlayerdown", "notpresent"}:
        return "down"
    if oper_status in {"testing", "3", "dormant", "5"}:
        return "degraded"
    if oper_status in {"unknown", "4"}:
        return "unknown"
    return _normalize_status(oper_status)


def _normalize_librenms_service_status(service: dict[str, Any]) -> str:
    if str(service.get("service_disabled") or "").strip() in {"1", "true", "yes"}:
        return "unconfigured"
    if str(service.get("service_ignore") or "").strip() in {"1", "true", "yes"}:
        return "unconfigured"
    raw_status = str(service.get("service_status") or "").strip().lower()
    if raw_status in {"0", "ok", "up"}:
        return "healthy"
    if raw_status in {"1", "warning"}:
        return "degraded"
    if raw_status in {"2", "critical", "down"}:
        return "down"
    if raw_status in {"3", "unknown"}:
        return "unknown"
    return _normalize_status(raw_status)


def _librenms_service_reference(service: dict[str, Any]) -> dict[str, str]:
    fields = {
        "service_id": str(service.get("service_id") or "").strip(),
        "device_id": str(service.get("device_id") or "").strip(),
        "service_ip": str(service.get("service_ip") or service.get("ip") or "").strip(),
        "service_name": str(service.get("service_name") or "").strip(),
        "service_desc": str(service.get("service_desc") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _librenms_device_reference(device: dict[str, Any]) -> dict[str, str]:
    fields = {
        "device_id": str(device.get("device_id") or "").strip(),
        "hostname": str(device.get("hostname") or "").strip(),
        "sys_name": str(device.get("sys_name") or "").strip(),
        "display": str(device.get("display") or "").strip(),
        "ip": str(device.get("ip") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _librenms_interface_reference(interface: dict[str, Any]) -> dict[str, str]:
    fields = {
        "port_id": str(interface.get("port_id") or "").strip(),
        "device_id": str(interface.get("device_id") or "").strip(),
        "if_index": str(interface.get("if_index") or "").strip(),
        "if_name": str(interface.get("if_name") or "").strip(),
        "if_descr": str(interface.get("if_descr") or "").strip(),
        "if_alias": str(interface.get("if_alias") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _librenms_interface_label(interface: dict[str, Any]) -> str:
    return str(interface.get("if_alias") or interface.get("if_name") or interface.get("if_descr") or "").strip()


def _librenms_interface_detail(interface: dict[str, Any]) -> str:
    label = _librenms_interface_label(interface)
    oper_status = str(interface.get("if_oper_status") or "").strip()
    admin_status = str(interface.get("if_admin_status") or "").strip()
    parts = []
    if label:
        parts.append(label)
    if oper_status:
        parts.append(f"oper {oper_status}")
    if admin_status:
        parts.append(f"admin {admin_status}")
    return ", ".join(parts)


def _compact_match_text(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _alert_summary(alert: dict[str, Any]) -> str:
    description = str(alert.get("description") or "").strip()
    hostname = str(alert.get("hostname") or "").strip()
    severity = str(alert.get("severity") or "").strip()
    if description and hostname and severity:
        return f"{description} on {hostname} is {severity}."
    if description and hostname:
        return f"{description} on {hostname}."
    return description or hostname


def _build_dependencies(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    probe: dict[str, Any],
    evidence: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    probe_status = _normalize_status(str(probe.get("status") or "unknown"), detail=str(probe.get("detail") or ""))
    dependencies.append(
        {
            "id": "internet",
            "display_name": "Internet",
            "status": probe_status,
            "severity": _severity_for_status(probe_status),
            "freshness": _aggregate_freshness([item for item in evidence if item["id"].startswith("probe.")]),
            "summary": str(probe.get("detail") or _summary_for_status(probe_status)),
            "evidence_ids": [item["id"] for item in evidence if item["id"].startswith("probe.")],
        }
    )

    for raw in inventory.get("dependencies") or []:
        if not isinstance(raw, dict):
            continue
        dependency_id = str(raw.get("id") or "").strip()
        if not dependency_id:
            continue
        monitor_evidence = _evidence_for_target(
            monitors=monitors,
            evidence=evidence,
            target_type="dependency",
            target_id=dependency_id,
        )
        status = _aggregate_status([str(item.get("status") or "unknown") for item in monitor_evidence])
        dependencies.append(
            {
                "id": dependency_id,
                "display_name": str(raw.get("display_name") or dependency_id).strip() or dependency_id,
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness(monitor_evidence),
                "summary": _summary_for_status(status),
                "evidence_ids": [item["id"] for item in monitor_evidence],
                "from_type": str(raw.get("from_type") or "").strip().lower(),
                "from_id": str(raw.get("from_id") or "").strip(),
                "to_type": str(raw.get("to_type") or "").strip().lower(),
                "to_id": str(raw.get("to_id") or "").strip(),
                "relationship": str(raw.get("relationship") or "depends_on").strip().lower(),
            }
        )
    return dependencies


def _build_hosts(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    monitors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hosts: list[dict[str, Any]] = []
    for raw in inventory.get("hosts") or []:
        if not isinstance(raw, dict):
            continue
        host_id = str(raw.get("id") or "").strip()
        if not host_id:
            continue
        monitor_statuses = [
            str(monitor.get("status") or "unknown")
            for monitor in monitors
            if monitor.get("target_type") == "host" and monitor.get("target_id") == host_id
        ]
        status = _aggregate_status(monitor_statuses)
        hosts.append(
            {
                "id": host_id,
                "display_name": str(raw.get("display_name") or host_id).strip() or host_id,
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness_for_monitors(monitors, target_type="host", target_id=host_id),
                "summary": _summary_for_status(status),
                "evidence_ids": _evidence_ids_for_monitors(monitors, target_type="host", target_id=host_id),
                "kind": str(raw.get("kind") or "").strip(),
                "role": str(raw.get("role") or "").strip(),
                "description": str(raw.get("description") or "").strip(),
                "address_label": ", ".join(str(item).strip() for item in raw.get("addresses") or [] if str(item).strip()),
            }
        )
    return hosts


def _build_services(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    monitors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for raw in inventory.get("services") or []:
        if not isinstance(raw, dict):
            continue
        service_id = str(raw.get("id") or "").strip()
        if not service_id:
            continue
        status = _aggregate_status(
            [
                str(monitor.get("status") or "unknown")
                for monitor in monitors
                if monitor.get("target_type") == "service" and monitor.get("target_id") == service_id
            ]
        )
        services.append(
            {
                "id": service_id,
                "display_name": str(raw.get("display_name") or service_id).strip() or service_id,
                "host_id": str(raw.get("host_id") or "").strip(),
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness_for_monitors(monitors, target_type="service", target_id=service_id),
                "summary": _summary_for_status(status),
                "evidence_ids": _evidence_ids_for_monitors(monitors, target_type="service", target_id=service_id),
                "kind": str(raw.get("kind") or "").strip(),
                "description": str(raw.get("description") or "").strip(),
            }
        )
    return services


def _build_service_groups(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    services: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    service_by_id = {str(service.get("id") or ""): service for service in services}
    groups: list[dict[str, Any]] = []
    for raw in inventory.get("service_groups") or []:
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("id") or "").strip()
        service_ids = [str(item).strip() for item in raw.get("service_ids") or [] if str(item).strip()]
        if not group_id or not service_ids:
            continue
        group_services = [service_by_id[item] for item in service_ids if item in service_by_id]
        status = _aggregate_status([str(service.get("status") or "unknown") for service in group_services])
        groups.append(
            {
                "id": group_id,
                "display_name": str(raw.get("display_name") or group_id).strip() or group_id,
                "host_id": str(raw.get("host_id") or "").strip(),
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness(group_services),
                "summary": _summary_for_status(status),
                "service_ids": service_ids,
                "evidence_ids": _unique_evidence_ids(group_services),
                "collapsed": bool(raw.get("collapsed", True)),
            }
        )
    return groups


def build_network_admin_payload(
    snapshot: dict[str, Any],
    *,
    control_policy: dict[str, list[dict[str, Any]]] | None = None,
    control_results: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    control_availability: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    monitors = [item for item in snapshot.get("monitors") or [] if isinstance(item, dict)]
    evidence = [item for item in snapshot.get("evidence") or [] if isinstance(item, dict)]
    control_actions = _build_control_actions_by_target(
        control_policy or {"actions": []},
        control_results=control_results or {},
        control_availability=control_availability or {},
    )
    services = [
        _with_control_actions(
            "service",
            _with_monitoring_diagnostics("service", item, monitors=monitors),
            control_actions=control_actions,
        )
        for item in snapshot.get("services") or []
        if isinstance(item, dict)
    ]
    service_groups = [
        _with_service_group_diagnostics(item, services=services, monitors=monitors)
        for item in snapshot.get("service_groups") or []
        if isinstance(item, dict)
    ]
    services_by_host = _items_by_key(services, key="host_id")
    services_by_id = {
        str(service.get("id") or ""): service
        for service in services
    }
    groups_by_host = _items_by_key(service_groups, key="host_id")
    power_targets_by_host = _items_by_key(
        [item for item in snapshot.get("power_targets") or [] if isinstance(item, dict)],
        key="host_id",
    )
    grouped_service_ids = {
        str(service_id)
        for group in service_groups
        for service_id in group.get("service_ids") or []
    }
    hosts: list[dict[str, Any]] = []
    for raw_host in snapshot.get("hosts") or []:
        if not isinstance(raw_host, dict):
            continue
        host_id = str(raw_host.get("id") or "").strip()
        if not host_id:
            continue
        ungrouped_services = [
            service
            for service in services_by_host.get(host_id, [])
            if str(service.get("id") or "") not in grouped_service_ids
        ]
        host_power_actions: list[dict[str, Any]] = []
        for power_target in power_targets_by_host.get(host_id, []):
            power_target_id = str(power_target.get("id") or "").strip()
            for action in control_actions.get(("power_target", power_target_id), []):
                host_power_actions.append(
                    {
                        **action,
                        "target_type": "power_target",
                        "target_id": power_target_id,
                    }
                )
        host_with_actions = _with_control_actions(
            "host",
            _with_monitoring_diagnostics("host", raw_host, monitors=monitors),
            control_actions=control_actions,
        )
        if host_power_actions:
            host_with_actions = {
                **host_with_actions,
                "control_actions": [
                    *host_with_actions.get("control_actions", []),
                    *host_power_actions,
                ],
            }
        hosts.append(
            {
                **host_with_actions,
                "service_groups": [
                    {
                        **group,
                        "services": [
                            services_by_id[str(service_id)]
                            for service_id in group.get("service_ids") or []
                            if str(service_id) in services_by_id
                        ],
                    }
                    for group in groups_by_host.get(host_id, [])
                ],
                "services": ungrouped_services,
            }
        )
    return {
        "status": snapshot.get("status"),
        "severity": snapshot.get("severity"),
        "freshness": snapshot.get("freshness"),
        "generated_at": snapshot.get("generated_at"),
        "cached_at": snapshot.get("cached_at"),
        "cache_age_seconds": snapshot.get("cache_age_seconds"),
        "cache_ttl_seconds": snapshot.get("cache_ttl_seconds"),
        "cache_hit": snapshot.get("cache_hit"),
        "summary": snapshot.get("summary"),
        "hosts": hosts,
        "ungrouped_services": [item for item in services if not item.get("host_id")],
        "dependencies": snapshot.get("dependencies") or [],
        "monitors": [_with_monitor_diagnostics(item) for item in monitors],
        "evidence": evidence,
        "coverage": _build_inventory_coverage(hosts=hosts, services=services, monitors=monitors),
        "provider_diagnostics": _build_provider_diagnostics(snapshot.get("provider_observations"), monitors=monitors),
    }


def _build_control_actions_by_target(
    control_policy: dict[str, list[dict[str, Any]]],
    *,
    control_results: dict[tuple[str, str, str], dict[str, Any]],
    control_availability: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    actions_by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in control_policy.get("actions") or []:
        if not isinstance(raw, dict):
            continue
        target_type = str(raw.get("target_type") or "").strip().lower()
        target_id = str(raw.get("target_id") or "").strip()
        action_id = str(raw.get("action_id") or "").strip()
        if not target_type or not target_id or not action_id:
            continue
        item = {
            "id": str(raw.get("id") or "").strip(),
            "target_type": target_type,
            "target_id": target_id,
            "action_id": action_id,
            "enabled": raw.get("enabled") is True,
            "requires_confirmation": raw.get("requires_confirmation") is True,
            "adapter": str(raw.get("adapter") or "").strip(),
            "provider": str(raw.get("provider") or "").strip(),
            "description": str(raw.get("description") or "").strip(),
        }
        last_result = control_results.get((target_type, target_id, action_id))
        if isinstance(last_result, dict):
            item["last_control_result"] = _safe_control_result(last_result)
        availability = control_availability.get((target_type, target_id, action_id))
        if isinstance(availability, dict):
            item["availability"] = _safe_control_availability(availability)
        actions_by_target.setdefault((target_type, target_id), []).append(item)
    return actions_by_target


def _safe_control_availability(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key
        in {
            "status",
            "active_target_type",
            "active_target_id",
            "active_action_id",
            "active_started_at",
            "cooldown_remaining_seconds",
            "cooldown_until",
        }
    }


def _with_control_actions(
    target_type: str,
    item: dict[str, Any],
    *,
    control_actions: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    actions = control_actions.get((target_type, item_id), [])
    if not actions:
        return item
    last_result = _latest_control_result(actions)
    return {
        **item,
        "control_actions": actions,
        **({"last_control_result": last_result} if last_result else {}),
    }


def _latest_control_result(actions: list[dict[str, Any]]) -> dict[str, Any]:
    results = [
        action.get("last_control_result")
        for action in actions
        if isinstance(action.get("last_control_result"), dict)
    ]
    if not results:
        return {}
    return max(results, key=lambda item: str(item.get("recorded_at") or item.get("requested_at") or ""))


def _safe_control_result(raw: dict[str, Any]) -> dict[str, Any]:
    execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    return {
        "request_id": str(raw.get("request_id") or "").strip(),
        "recorded_at": str(raw.get("recorded_at") or "").strip(),
        "requested_at": str(raw.get("requested_at") or "").strip(),
        "actor": str(raw.get("actor") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
        "target_type": str(raw.get("target_type") or "").strip().lower(),
        "target_id": str(raw.get("target_id") or "").strip(),
        "action_id": str(raw.get("action_id") or "").strip(),
        "mode": str(raw.get("mode") or "").strip(),
        "provider": str(raw.get("provider") or "").strip(),
        "adapter": str(raw.get("adapter") or "").strip(),
        "policy_status": str(raw.get("policy_status") or "").strip(),
        "confirmation_status": str(raw.get("confirmation_status") or "").strip(),
        "result_status": str(raw.get("result_status") or "").strip(),
        "error_class": str(raw.get("error_class") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "execution": {
            key: value
            for key, value in dict(execution).items()
            if key
            in {
                "adapter",
                "method",
                "service_manager",
                "wait_seconds",
                "restart_timeout_seconds",
                "shutdown_timeout_seconds",
                "recovery_timeout_seconds",
                "recovery_poll_seconds",
                "readiness_timeout_seconds",
                "verification_status",
                "readiness_status",
                "readiness_check_count",
                "readiness_passed_count",
                "power_restored",
                "shutdown_observed",
                "local_restart_completed",
                "boot_changed",
                "deferred",
                "availability_status",
                "cooldown_seconds",
                "cooldown_remaining_seconds",
                "cooldown_until",
                "lifecycle_status",
                "lifecycle_completed_phase_ids",
            }
        },
    }


def _with_monitoring_diagnostics(
    target_type: str,
    item: dict[str, Any],
    *,
    monitors: list[dict[str, Any]],
) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    item_monitors = [
        monitor
        for monitor in monitors
        if str(monitor.get("target_type") or "").strip().lower() == target_type
        and str(monitor.get("target_id") or "").strip() == item_id
    ]
    evidence_count = len([value for value in item.get("evidence_ids") or [] if str(value).strip()])
    monitor_count = len(item_monitors)
    return {
        **item,
        "monitor_count": monitor_count,
        "evidence_count": evidence_count,
        "monitoring_state": _monitoring_state(monitor_count=monitor_count, evidence_count=evidence_count),
    }


def _with_service_group_diagnostics(
    group: dict[str, Any],
    *,
    services: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
) -> dict[str, Any]:
    service_ids = {str(value).strip() for value in group.get("service_ids") or [] if str(value).strip()}
    grouped_services = [service for service in services if str(service.get("id") or "") in service_ids]
    monitor_count = len(
        [
            monitor
            for monitor in monitors
            if str(monitor.get("target_type") or "").strip().lower() == "service"
            and str(monitor.get("target_id") or "").strip() in service_ids
        ]
    )
    evidence_count = len(_unique_evidence_ids(grouped_services))
    return {
        **group,
        "monitor_count": monitor_count,
        "evidence_count": evidence_count,
        "monitoring_state": _monitoring_state(monitor_count=monitor_count, evidence_count=evidence_count),
    }


def _with_monitor_diagnostics(monitor: dict[str, Any]) -> dict[str, Any]:
    evidence_count = len([value for value in monitor.get("evidence_ids") or [] if str(value).strip()])
    return {
        **monitor,
        "evidence_count": evidence_count,
        "evidence_matched": evidence_count > 0,
    }


def _monitoring_state(*, monitor_count: int, evidence_count: int) -> str:
    if monitor_count <= 0:
        return "unmonitored"
    if evidence_count <= 0:
        return "configured_no_evidence"
    return "monitored"


def _build_inventory_coverage(
    *,
    hosts: list[dict[str, Any]],
    services: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "hosts": _coverage_summary(hosts),
        "services": _coverage_summary(services),
        "monitors": _monitor_coverage_summary(monitors),
    }


def _coverage_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "id": str(item.get("id") or ""),
            "display_name": str(item.get("display_name") or item.get("id") or ""),
            "monitor_count": int(item.get("monitor_count") or 0),
            "evidence_count": int(item.get("evidence_count") or 0),
            "monitoring_state": str(item.get("monitoring_state") or "unmonitored"),
        }
        for item in items
    ]
    return {
        "total": len(rows),
        "monitored": len([item for item in rows if item["monitoring_state"] == "monitored"]),
        "configured_no_evidence": len([item for item in rows if item["monitoring_state"] == "configured_no_evidence"]),
        "unmonitored": len([item for item in rows if item["monitoring_state"] == "unmonitored"]),
        "items": rows,
    }


def _monitor_coverage_summary(monitors: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for monitor in monitors:
        evidence_count = len([value for value in monitor.get("evidence_ids") or [] if str(value).strip()])
        rows.append(
            {
                "id": str(monitor.get("id") or ""),
                "display_name": str(monitor.get("display_name") or monitor.get("id") or ""),
                "target_type": str(monitor.get("target_type") or ""),
                "target_id": str(monitor.get("target_id") or ""),
                "provider": str(monitor.get("provider") or ""),
                "evidence_count": evidence_count,
                "evidence_matched": evidence_count > 0,
            }
        )
    return {
        "total": len(rows),
        "with_evidence": len([item for item in rows if item["evidence_matched"]]),
        "without_evidence": len([item for item in rows if not item["evidence_matched"]]),
        "items": rows,
    }


def _build_provider_diagnostics(observations: Any, *, monitors: list[dict[str, Any]]) -> dict[str, Any]:
    raw_services = []
    if isinstance(observations, dict) and isinstance(observations.get("librenms_services"), list):
        raw_services = [item for item in observations.get("librenms_services") or [] if isinstance(item, dict)]
    raw_interfaces = []
    if isinstance(observations, dict) and isinstance(observations.get("librenms_interfaces"), list):
        raw_interfaces = [item for item in observations.get("librenms_interfaces") or [] if isinstance(item, dict)]
    declared_monitor_ids = {
        str(monitor.get("id") or "").strip()
        for monitor in monitors
        if str(monitor.get("provider") or "").strip().lower() == "librenms"
        and str(monitor.get("target_type") or "").strip().lower() == "service"
    }
    rows: list[dict[str, Any]] = []
    for service in raw_services:
        matched_monitor_ids = [
            str(value).strip()
            for value in service.get("matched_monitor_ids") or []
            if str(value).strip() in declared_monitor_ids
        ]
        rows.append(
            {
                "service_id": str(service.get("service_id") or ""),
                "device_id": str(service.get("device_id") or ""),
                "service_ip": str(service.get("service_ip") or ""),
                "service_name": str(service.get("service_name") or ""),
                "service_desc": str(service.get("service_desc") or ""),
                "service_type": str(service.get("service_type") or ""),
                "status": str(service.get("status") or "unknown"),
                "summary": str(service.get("summary") or ""),
                "matched_monitor_ids": matched_monitor_ids,
            }
        )
    declared_interface_monitor_ids = {
        str(monitor.get("id") or "").strip()
        for monitor in monitors
        if str(monitor.get("provider") or "").strip().lower() == "librenms"
        and str(monitor.get("kind") or "").strip().lower() == "interface"
        and str(monitor.get("target_type") or "").strip().lower() in {"dependency", "host"}
        and str(monitor.get("id") or "").strip()
    }
    interface_rows: list[dict[str, Any]] = []
    for interface in raw_interfaces:
        matched_monitor_ids = [
            str(value).strip()
            for value in interface.get("matched_monitor_ids") or []
            if str(value).strip() in declared_interface_monitor_ids
        ]
        interface_rows.append(
            {
                "port_id": str(interface.get("port_id") or ""),
                "device_id": str(interface.get("device_id") or ""),
                "if_index": str(interface.get("if_index") or ""),
                "if_name": str(interface.get("if_name") or ""),
                "if_descr": str(interface.get("if_descr") or ""),
                "if_alias": str(interface.get("if_alias") or ""),
                "if_oper_status": str(interface.get("if_oper_status") or ""),
                "if_admin_status": str(interface.get("if_admin_status") or ""),
                "status": str(interface.get("status") or "unknown"),
                "summary": str(interface.get("summary") or ""),
                "matched_monitor_ids": matched_monitor_ids,
            }
        )
    return {
        "librenms_services": {
            "total": len(rows),
            "matched": len([item for item in rows if item["matched_monitor_ids"]]),
            "unmatched": len([item for item in rows if not item["matched_monitor_ids"]]),
            "items": rows,
        },
        "librenms_interfaces": {
            "total": len(interface_rows),
            "matched": len([item for item in interface_rows if item["matched_monitor_ids"]]),
            "unmatched": len([item for item in interface_rows if not item["matched_monitor_ids"]]),
            "items": interface_rows,
        },
    }


def _items_by_key(items: Any, *, key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        grouped.setdefault(value, []).append(item)
    return grouped


def _unique_evidence_ids(items: list[dict[str, Any]]) -> list[str]:
    evidence_ids: list[str] = []
    for item in items:
        for evidence_id in item.get("evidence_ids") or []:
            text = str(evidence_id)
            if text not in evidence_ids:
                evidence_ids.append(text)
    return evidence_ids


def _evidence_for_target(
    *,
    monitors: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    target_type: str,
    target_id: str,
) -> list[dict[str, Any]]:
    evidence_ids = _evidence_ids_for_monitors(monitors, target_type=target_type, target_id=target_id)
    return [item for item in evidence if item["id"] in evidence_ids]


def _evidence_ids_for_monitors(
    monitors: list[dict[str, Any]],
    *,
    target_type: str,
    target_id: str,
) -> list[str]:
    evidence_ids: list[str] = []
    for monitor in monitors:
        if monitor.get("target_type") != target_type or monitor.get("target_id") != target_id:
            continue
        for evidence_id in monitor.get("evidence_ids") or []:
            if evidence_id not in evidence_ids:
                evidence_ids.append(str(evidence_id))
    return evidence_ids


def _aggregate_freshness_for_monitors(
    monitors: list[dict[str, Any]],
    *,
    target_type: str,
    target_id: str,
) -> str:
    matched = [
        {"freshness": monitor.get("freshness")}
        for monitor in monitors
        if monitor.get("target_type") == target_type and monitor.get("target_id") == target_id
    ]
    return _aggregate_freshness(matched)


def _normalize_status(status: str, *, detail: str = "") -> str:
    normalized = status.strip().lower()
    normalized_detail = detail.strip().lower()
    if normalized in {"healthy", "degraded", "down", "unknown", "unconfigured", "unavailable", "stale"}:
        if normalized == "unknown" and ("not configured" in normalized_detail or "disabled" in normalized_detail):
            return "unconfigured"
        return normalized
    if normalized in {"ok", "up"}:
        return "healthy"
    if normalized in {"failed", "error"}:
        return "unavailable"
    return "unknown"


def _aggregate_status(statuses: list[str]) -> str:
    normalized = [_normalize_status(status) for status in statuses if str(status).strip()]
    if not normalized:
        return "unknown"
    if "down" in normalized:
        return "down"
    if "degraded" in normalized:
        return "degraded"
    if "unavailable" in normalized:
        return "unavailable"
    if "stale" in normalized:
        return "stale"
    if "healthy" in normalized and all(status in {"healthy", "unconfigured", "unknown"} for status in normalized):
        return "healthy"
    if all(status == "unconfigured" for status in normalized):
        return "unconfigured"
    if all(status in {"unknown", "unconfigured"} for status in normalized):
        return "unknown"
    return "unknown"


def _severity_for_status(status: str) -> str:
    return {
        "healthy": "none",
        "degraded": "warning",
        "down": "critical",
        "unavailable": "warning",
        "unconfigured": "info",
        "stale": "warning",
        "unknown": "unknown",
    }.get(status, "unknown")


def _aggregate_freshness(items: list[dict[str, Any]]) -> str:
    freshness_values = [str(item.get("freshness") or "unknown") for item in items]
    if not freshness_values:
        return "unknown"
    if "fresh" in freshness_values:
        return "fresh"
    if "aging" in freshness_values:
        return "aging"
    if "stale" in freshness_values:
        return "stale"
    return "unknown"


def _summary_for_status(status: str) -> str:
    if status == "healthy":
        return "No problems are known."
    if status == "degraded":
        return "One or more observations are degraded."
    if status == "down":
        return "One or more observations are down."
    if status == "unavailable":
        return "A provider or dependency is unavailable."
    if status == "unconfigured":
        return "Monitoring is not configured."
    if status == "stale":
        return "The latest observation is stale."
    return "Status is unknown."


def _build_provider_observations(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    monitoring: dict[str, Any],
) -> dict[str, Any]:
    services = [item for item in monitoring.get("services") or [] if isinstance(item, dict)]
    interfaces = [item for item in monitoring.get("interfaces") or [] if isinstance(item, dict)]
    service_monitors = [
        item
        for item in inventory.get("monitors") or []
        if isinstance(item, dict)
        and str(item.get("source") or "").strip().lower() == "librenms"
        and str(item.get("kind") or "").strip().lower() == "service"
    ]
    interface_monitors = [
        item
        for item in inventory.get("monitors") or []
        if isinstance(item, dict)
        and str(item.get("source") or "").strip().lower() == "librenms"
        and str(item.get("kind") or "").strip().lower() == "interface"
    ]
    rows: list[dict[str, Any]] = []
    for service in services:
        matched_monitor_ids = [
            str(monitor.get("id") or "").strip()
            for monitor in service_monitors
            if _librenms_service_matches_monitor(service, monitor)
        ]
        rows.append(
            {
                **_librenms_service_reference(service),
                "service_type": str(service.get("service_type") or "").strip(),
                "status": _normalize_librenms_service_status(service),
                "summary": str(service.get("service_message") or service.get("service_desc") or "").strip(),
                "matched_monitor_ids": [value for value in matched_monitor_ids if value],
            }
        )
    interface_rows: list[dict[str, Any]] = []
    for interface in interfaces:
        matched_monitor_ids = [
            str(monitor.get("id") or "").strip()
            for monitor in interface_monitors
            if _librenms_interface_matches_monitor(interface, monitor)
        ]
        interface_rows.append(
            {
                **_librenms_interface_reference(interface),
                "if_oper_status": str(interface.get("if_oper_status") or "").strip(),
                "if_admin_status": str(interface.get("if_admin_status") or "").strip(),
                "status": _normalize_librenms_interface_status(interface),
                "summary": _librenms_interface_detail(interface),
                "matched_monitor_ids": [value for value in matched_monitor_ids if value],
            }
        )
    return {
        "librenms_services": rows,
        "librenms_interfaces": interface_rows,
    }
