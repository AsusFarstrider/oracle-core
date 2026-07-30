from __future__ import annotations

import shlex
import subprocess
from typing import Any

from oracle_app.configuration.domain_models import ServiceControlAdapter
from oracle_app.configuration.network_adapter_runtime_settings import (
    NetworkAdapterRuntimeSettings,
    NetworkAdaptersRuntimeSettings,
)
from oracle_app.provider_bridges.service_control import (
    _check_json_health,
    _mount_options,
    _mount_target,
    _raid_array_healthy,
    _service_state_argv,
    _service_status_argv,
    _typed_transport_command,
    check_typed_service_available,
)


class TypedServiceControl:
    """Execute only finite service-control adapters from one canonical snapshot."""

    def __init__(self, adapters: NetworkAdaptersRuntimeSettings) -> None:
        self.adapters = adapters

    def lifecycle_plan(self, host: NetworkAdapterRuntimeSettings) -> dict[str, Any]:
        definition = self._host_definition(host)
        lifecycle = definition.lifecycle
        if lifecycle is None:
            return {"configured": False, "mode": "", "phases": [], "summary": "Graceful host lifecycle is not configured."}
        phases: list[dict[str, str]] = []
        if lifecycle.client_release is not None:
            phases.append({"id": "release_client_storage", "kind": "preparation", "summary": "Stop dependent Oracle services and release the client storage mount."})
        if lifecycle.prepare_service_adapter_ids:
            phases.append({"id": "stop_host_services", "kind": "preparation", "summary": f"Stop {len(lifecycle.prepare_service_adapter_ids)} configured host service(s) cleanly."})
        if lifecycle.storage is not None:
            phases.append({"id": "close_host_storage", "kind": "preparation", "summary": "Stop storage sharing, flush writes, unmount storage, and stop the RAID array."})
        phases.extend([
            {"id": "restart_host", "kind": "execution", "summary": "Restart the host only after all mandatory preparation phases pass."},
            {"id": "verify_host_recovery", "kind": "verification", "summary": "Verify host recovery and configured readiness checks."},
        ])
        if lifecycle.client_release is not None:
            phases.append({"id": "restore_client_storage", "kind": "recovery", "summary": "Restore the client storage mount and restart dependent Oracle services."})
        return {"configured": True, "mode": "graceful", "phases": phases, "summary": f"Graceful host lifecycle has {len(phases)} mandatory phase(s)."}

    def check_readiness(
        self,
        host: NetworkAdapterRuntimeSettings,
        *,
        timeout_seconds: int = 8,
    ) -> dict[str, Any]:
        definition = self._host_definition(host)
        checks: list[dict[str, str]] = []
        for adapter_id in definition.readiness_service_adapter_ids:
            service = self._service(adapter_id)
            result = check_typed_service_available(
                adapter=service.definition,
                credential=service.credential,
                timeout_seconds=timeout_seconds,
            )
            checks.append({"id": f"service:{adapter_id}", "kind": "service", "status": "passed" if result.get("ok") is True else "failed"})
        for url in definition.readiness_http_urls:
            result = _check_json_health(url=str(url), timeout_seconds=timeout_seconds)
            checks.append({"id": f"http:{url}", "kind": "http", "status": "passed" if result.get("ok") is True else "failed"})
        for path in definition.readiness_read_write_paths:
            mount = self._run(host, ["findmnt", "-rn", "-o", "TARGET", str(path)], timeout_seconds)
            writable = mount.get("ok") is True and str(mount.get("stdout") or "").strip() == str(path) and self._write_probe(host, str(path), timeout_seconds)
            checks.append({"id": f"mount:{path}", "kind": "mount", "status": "passed" if writable else "failed"})
        failed = [item for item in checks if item["status"] != "passed"]
        return {
            "ok": bool(checks) and not failed,
            "status": "passed" if checks and not failed else "failed",
            "check_count": len(checks),
            "passed_count": len(checks) - len(failed),
            "failed_check_ids": [item["id"] for item in failed],
            "checks": checks,
            "detail": "All configured host readiness checks passed." if checks and not failed else "One or more configured host readiness checks did not pass.",
        }

    def check_storage_safety(self, host: NetworkAdapterRuntimeSettings, *, timeout_seconds: int = 8) -> dict[str, Any]:
        definition = self._host_definition(host)
        storage = None if definition.lifecycle is None else definition.lifecycle.storage
        if storage is None:
            return {"ok": False, "configured": False, "status": "unavailable", "check_count": 0, "passed_count": 0, "failed_check_ids": [], "checks": [], "detail": "Storage safety checks are not configured."}
        mdstat = self._run(host, ["cat", "/proc/mdstat"], timeout_seconds)
        mount = self._run(host, ["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", str(storage.mount_path)], timeout_seconds)
        sharing = self._service(storage.sharing_service_adapter_id)
        service = check_typed_service_available(adapter=sharing.definition, credential=sharing.credential, timeout_seconds=timeout_seconds)
        mount_text = str(mount.get("stdout") or "").strip()
        checks = [
            {"id": "raid", "status": "passed" if mdstat.get("ok") is True and _raid_array_healthy(str(mdstat.get("stdout") or ""), array_name=storage.array_id) else "failed"},
            {"id": "mount", "status": "passed" if mount.get("ok") is True and _mount_target(mount_text) == str(storage.mount_path) and "rw" in _mount_options(mount_text) else "failed"},
            {"id": "sharing_service", "status": "passed" if service.get("ok") is True else "failed"},
        ]
        passed = sum(item["status"] == "passed" for item in checks)
        return {"ok": passed == len(checks), "configured": True, "status": "passed" if passed == len(checks) else "failed", "check_count": len(checks), "passed_count": passed, "failed_check_ids": [item["id"] for item in checks if item["status"] != "passed"], "checks": checks, "detail": "Storage safety checks completed."}

    def prepare(self, host: NetworkAdapterRuntimeSettings, *, timeout_seconds: int = 30) -> dict[str, Any]:
        definition = self._host_definition(host)
        lifecycle = definition.lifecycle
        if lifecycle is None:
            return _failure("service_control_lifecycle_not_configured", "Graceful host lifecycle is not configured.")
        completed: list[str] = []
        if lifecycle.client_release is not None:
            result = self._release_client(lifecycle.client_release, timeout_seconds)
            if result.get("ok") is not True:
                return result
            completed.append("release_client_storage")
        if lifecycle.prepare_service_adapter_ids:
            result = self._set_services(lifecycle.prepare_service_adapter_ids, "stopped", timeout_seconds)
            if result.get("ok") is not True:
                self._restore_client(lifecycle.client_release, timeout_seconds)
                return result
            completed.append("stop_host_services")
        if lifecycle.storage is not None:
            result = self._close_storage(host, lifecycle.storage, timeout_seconds)
            if result.get("ok") is not True:
                self.rollback(host, completed, timeout_seconds=timeout_seconds)
                return result
            completed.append("close_host_storage")
        return {"ok": True, "status": "prepared", "completed_phase_ids": completed, "detail": "All configured graceful host preparation phases passed."}

    def rollback(self, host: NetworkAdapterRuntimeSettings, completed: list[str], *, timeout_seconds: int = 30) -> dict[str, Any]:
        definition = self._host_definition(host)
        lifecycle = definition.lifecycle
        if lifecycle is None:
            return _failure("service_control_lifecycle_not_configured", "Graceful host lifecycle is not configured.")
        errors: list[str] = []
        if "close_host_storage" in completed and lifecycle.storage is not None:
            storage = lifecycle.storage
            for command in (["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mdadm", "--assemble", f"/dev/{storage.array_id}"], ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mount", str(storage.mount_path)]):
                if self._run(host, list(command), timeout_seconds).get("ok") is not True:
                    errors.append("storage")
            if self._set_services([storage.sharing_service_adapter_id], "started", timeout_seconds).get("ok") is not True:
                errors.append("sharing_service")
        if "stop_host_services" in completed and self._set_services(lifecycle.prepare_service_adapter_ids, "started", timeout_seconds).get("ok") is not True:
            errors.append("host_services")
        if "release_client_storage" in completed and self._restore_client(lifecycle.client_release, timeout_seconds).get("ok") is not True:
            errors.append("client_storage")
        return {"ok": not errors, "status": "rolled_back" if not errors else "rollback_failed", "failed_phase_ids": sorted(set(errors)), "detail": "Host preparation rollback completed." if not errors else "Host preparation rollback did not fully complete."}

    def recover(self, host: NetworkAdapterRuntimeSettings, *, timeout_seconds: int = 60) -> dict[str, Any]:
        host_services = self.recover_host_services(host, timeout_seconds=timeout_seconds)
        if host_services.get("ok") is not True:
            return host_services
        client = self.recover_client(host, timeout_seconds=timeout_seconds)
        if client.get("ok") is not True:
            return client
        completed = [
            *list(host_services.get("completed_phase_ids") or []),
            *list(client.get("completed_phase_ids") or []),
        ]
        return {"ok": True, "status": "recovered", "completed_phase_ids": completed, "detail": "Graceful lifecycle dependents were restored."}

    def recover_host_services(self, host: NetworkAdapterRuntimeSettings, *, timeout_seconds: int = 60) -> dict[str, Any]:
        lifecycle = self._host_definition(host).lifecycle
        if lifecycle is None:
            return _failure("service_control_lifecycle_not_configured", "Graceful host lifecycle is not configured.")
        if lifecycle.prepare_service_adapter_ids:
            result = self._set_services(lifecycle.prepare_service_adapter_ids, "started", timeout_seconds)
            if result.get("ok") is not True:
                return result
            return {"ok": True, "status": "recovered", "completed_phase_ids": ["restore_host_services"]}
        return {"ok": True, "status": "not_required", "completed_phase_ids": []}

    def recover_client(self, host: NetworkAdapterRuntimeSettings, *, timeout_seconds: int = 60) -> dict[str, Any]:
        lifecycle = self._host_definition(host).lifecycle
        if lifecycle is None:
            return _failure("service_control_lifecycle_not_configured", "Graceful host lifecycle is not configured.")
        if lifecycle.client_release is not None:
            result = self._restore_client(lifecycle.client_release, timeout_seconds)
            if result.get("ok") is not True:
                return result
            return {"ok": True, "status": "recovered", "completed_phase_ids": ["restore_client_storage"]}
        return {"ok": True, "status": "not_required", "completed_phase_ids": []}

    def _release_client(self, profile: Any, timeout: int) -> dict[str, Any]:
        result = self._set_services(profile.service_adapter_ids, "stopped", timeout)
        if result.get("ok") is not True:
            return result
        host = self._host_for_id(profile.host_id)
        result = self._run(host, ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "umount", str(profile.mount_path)], timeout)
        if result.get("ok") is not True:
            self._set_services(profile.service_adapter_ids, "started", timeout)
            return _failure("service_control_client_storage_release_failed", "Dependent services stopped, but the client storage mount could not be released.")
        return {"ok": True}

    def _restore_client(self, profile: Any | None, timeout: int) -> dict[str, Any]:
        if profile is None:
            return {"ok": True, "status": "not_required"}
        host = self._host_for_id(profile.host_id)
        result = self._run(host, ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "systemctl", "restart", profile.mount_service_target], timeout)
        if result.get("ok") is not True:
            return _failure("service_control_client_storage_restore_failed", "The client storage mount could not be restored.")
        if not self._mount_is_read_write(host, str(profile.mount_path), timeout):
            remounted = self._run(
                host,
                ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mount", "-o", "remount,rw", str(profile.mount_path)],
                timeout,
            )
            if remounted.get("ok") is not True or not self._mount_is_read_write(host, str(profile.mount_path), timeout):
                return _failure("service_control_client_storage_restore_failed", "The client storage mount did not recover read-write.")
        return self._set_services(profile.service_adapter_ids, "started", timeout)

    def _close_storage(self, host: NetworkAdapterRuntimeSettings, storage: Any, timeout: int) -> dict[str, Any]:
        result = self._set_services([storage.sharing_service_adapter_id], "stopped", timeout)
        if result.get("ok") is not True:
            return result
        commands = [
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "sync"],
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "umount", str(storage.mount_path)],
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mdadm", "--stop", f"/dev/{storage.array_id}"],
        ]
        for index, command in enumerate(commands):
            if self._run(host, command, timeout).get("ok") is not True:
                if index == 2:
                    self._run(host, ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mount", str(storage.mount_path)], timeout)
                self._set_services([storage.sharing_service_adapter_id], "started", timeout)
                return _failure("service_control_storage_close_failed", "Host storage could not be closed cleanly.")
        return {"ok": True}

    def _set_services(self, adapter_ids: Any, state: str, timeout: int) -> dict[str, Any]:
        completed: list[str] = []
        completed_targets: list[tuple[NetworkAdapterRuntimeSettings, str]] = []
        for adapter_id in adapter_ids:
            service = self._service(str(adapter_id))
            definition = service.definition
            targets = [*definition.lifecycle_service_targets, str(definition.service_target or "")]
            if state == "started":
                targets.reverse()
            for target in targets:
                argv = _service_state_argv(
                    adapter=str(definition.service_adapter or ""),
                    target=target,
                    desired_state=state,
                )
                result = self._run(service, argv, timeout)
                if result.get("ok") is not True:
                    self._rollback_stopped_targets(completed_targets, state, timeout)
                    return _failure("service_control_lifecycle_service_failed", f"Configured lifecycle service {adapter_id} could not be {state}.")
                completed_targets.append((service, target))
                if not self._target_has_state(service, target, state, timeout):
                    self._rollback_stopped_targets(completed_targets, state, timeout)
                    return _failure(
                        "service_control_lifecycle_service_verification_failed",
                        f"Configured lifecycle service {adapter_id} did not reach {state}.",
                    )
            completed.append(str(adapter_id))
        return {"ok": True, "completed_service_adapter_ids": completed}

    def _rollback_stopped_targets(
        self,
        completed: list[tuple[NetworkAdapterRuntimeSettings, str]],
        state: str,
        timeout: int,
    ) -> None:
        if state != "stopped":
            return
        for service, target in reversed(completed):
            definition = service.definition
            assert isinstance(definition, ServiceControlAdapter)
            self._run(
                service,
                _service_state_argv(
                    adapter=str(definition.service_adapter or ""),
                    target=target,
                    desired_state="started",
                ),
                timeout,
            )

    def _mount_is_read_write(
        self,
        host: NetworkAdapterRuntimeSettings,
        path: str,
        timeout: int,
    ) -> bool:
        result = self._run(host, ["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", path], timeout)
        text = str(result.get("stdout") or "").strip()
        return result.get("ok") is True and _mount_target(text) == path and "rw" in _mount_options(text)

    def _target_has_state(
        self,
        service: NetworkAdapterRuntimeSettings,
        target: str,
        state: str,
        timeout: int,
    ) -> bool:
        definition = service.definition
        assert isinstance(definition, ServiceControlAdapter)
        result = self._run(
            service,
            _service_status_argv(
                adapter=str(definition.service_adapter or ""),
                target=target,
                verification_mode=str(definition.verification_mode or ""),
            ),
            timeout,
        )
        if definition.service_adapter == "docker":
            running = str(result.get("stdout") or "").strip().lower() == "true"
        else:
            running = result.get("ok") is True
        return running if state == "started" else not running

    def _write_probe(self, host: NetworkAdapterRuntimeSettings, path: str, timeout: int) -> bool:
        script = 'probe="$1/.oracle-readiness-$$"; trap \'rm -f "$probe"\' EXIT HUP INT TERM; (umask 077 && printf "oracle-readiness\\n" > "$probe") && test -s "$probe" && rm -f "$probe"'
        command = ["sh", "-c", script, "oracle-readiness", path] if host.definition.transport == "local" else ["sh -c " + shlex.quote(script) + " oracle-readiness " + shlex.quote(path)]
        return self._run(host, command, timeout).get("ok") is True

    def _run(self, runtime: NetworkAdapterRuntimeSettings, command: list[str], timeout: int) -> dict[str, Any]:
        definition = runtime.definition
        if not isinstance(definition, ServiceControlAdapter):
            return _failure("service_control_adapter_invalid", "Adapter is not service control.")
        argv, stdin, environment = _typed_transport_command(adapter=definition, credential=runtime.credential, command_argv=command)
        if not argv:
            return _failure("service_control_transport_not_configured", "Service-control transport is not configured.")
        try:
            result = subprocess.run(argv, input=stdin, check=False, capture_output=True, text=True, timeout=max(3, min(60, int(timeout))), env=environment)
        except (OSError, subprocess.SubprocessError):
            return _failure("service_control_command_failed", "Service-control command could not be completed.")
        return {"ok": result.returncode == 0, "stdout": str(result.stdout or "") if result.returncode == 0 else ""}

    def _host_for_id(self, host_id: str) -> NetworkAdapterRuntimeSettings:
        matches = [item for item in self.adapters.adapters.values() if isinstance(item.definition, ServiceControlAdapter) and item.definition.target_kind == "host" and item.definition.host_id == host_id]
        if not matches:
            raise ValueError(f"No canonical host adapter exists for {host_id!r}.")
        return sorted(matches, key=lambda item: item.adapter_id)[0]

    def _service(self, adapter_id: str) -> NetworkAdapterRuntimeSettings:
        runtime = self.adapters.adapter(adapter_id)
        if runtime is None or not isinstance(runtime.definition, ServiceControlAdapter) or runtime.definition.target_kind != "service":
            raise ValueError(f"Canonical service adapter {adapter_id!r} is unavailable.")
        return runtime

    @staticmethod
    def _host_definition(runtime: NetworkAdapterRuntimeSettings) -> ServiceControlAdapter:
        definition = runtime.definition
        if not isinstance(definition, ServiceControlAdapter) or definition.target_kind != "host":
            raise ValueError("Canonical adapter does not target a host.")
        return definition


def _failure(error: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail, "completed_phase_ids": []}
