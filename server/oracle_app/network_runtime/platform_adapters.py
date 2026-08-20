from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import sys
from typing import Sequence
from urllib import error, request

from oracle_app.configuration.domain_models import RouterControlAdapter, ServiceControlAdapter

from .platform_transport import LocalPlatformTransport, PlatformTransport, SshPlatformTransport


_SYSTEMD_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
_DOCKER_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_WINDOWS_TASK_PATTERN = re.compile(r"^[A-Za-z0-9 _./\\:-]+$")


@dataclass(frozen=True)
class PlatformActionOutcome:
    ok: bool
    status: str = ""
    error: str = ""
    detail: str = ""
    platform: str = ""
    service_manager: str = ""
    deferred: bool = False


@dataclass(frozen=True)
class PlatformObservation:
    ok: bool
    status: str
    stdout: str = ""
    error: str = ""


class ServicePlatformAdapter:
    """Finite platform mechanics for one canonical service-control adapter."""

    def __init__(self, definition: ServiceControlAdapter, credential: str | None) -> None:
        self.definition = definition
        self.transport = _transport(definition, credential)

    def restart(self, operation: str, *, timeout_seconds: int = 15) -> PlatformActionOutcome:
        if self.definition.target_kind == "host":
            return self._restart_host(operation, timeout_seconds)
        if operation != "restart_service":
            return _failure("service_control_command_not_implemented", f"Command {operation} is not implemented.")
        if self.definition.transport == "local" and self.definition.restart_mode == "deferred_self_restart":
            return self._schedule_deferred_service_restart()
        stopped: list[str] = []
        for target in self.definition.lifecycle_service_targets:
            if not self.set_service_state(target, "stopped", timeout_seconds=timeout_seconds).ok:
                self._restore_companions(stopped, timeout_seconds)
                return _failure("service_control_lifecycle_service_failed", "A configured companion service could not be stopped.")
            stopped.append(target)
        command = self._service_restart_command(str(self.definition.service_target or ""))
        if not command:
            self._restore_companions(stopped, timeout_seconds)
            return _failure("service_control_adapter_not_implemented", "Configured service adapter is not implemented.")
        result = self.transport.run(command, timeout_seconds=max(5, timeout_seconds))
        if result.configuration_error:
            self._restore_companions(stopped, timeout_seconds)
            return _failure("service_control_transport_not_configured", "Service-control transport is not fully configured.")
        if result.timed_out:
            self._restore_companions(stopped, timeout_seconds)
            return _failure("service_control_command_timeout", "Service-control command timed out.")
        if not result.completed or result.returncode != 0:
            self._restore_companions(stopped, timeout_seconds)
            return _failure("service_control_command_failed", "Service-control command returned a failure.")
        if not self._restore_companions(stopped, timeout_seconds):
            return _failure("service_control_lifecycle_service_failed", "The primary service restarted, but a companion service could not be restored.")
        return PlatformActionOutcome(
            ok=True, status="executed", platform=self.definition.platform,
            service_manager=str(self.definition.service_adapter or ""),
            detail="Service-control command completed.",
        )

    def available(self, *, timeout_seconds: int = 8) -> PlatformObservation:
        if self.definition.target_kind != "service":
            return PlatformObservation(False, "failed", error="service_control_service_not_allowed")
        command = self._service_status_command(str(self.definition.service_target or ""))
        if not command:
            return PlatformObservation(False, "failed", error="service_control_status_not_implemented")
        result = self.transport.run(command, timeout_seconds=max(3, min(30, timeout_seconds)))
        if result.configuration_error:
            return PlatformObservation(False, "failed", error="service_control_transport_not_configured")
        if not result.completed:
            return PlatformObservation(False, "failed", error="service_control_status_failed")
        if self.definition.service_adapter == "systemd" and result.returncode == 3:
            return PlatformObservation(False, "failed")
        if self.definition.service_adapter == "docker" and result.returncode == 0 and result.stdout.strip().lower() != "true":
            return PlatformObservation(False, "failed")
        if result.returncode != 0:
            return PlatformObservation(False, "failed", error="service_control_status_failed")
        return PlatformObservation(True, "available", stdout=result.stdout)

    def set_service_state(self, target: str, state: str, *, timeout_seconds: int) -> PlatformActionOutcome:
        command = self._service_state_command(target, state)
        if not command:
            return _failure("service_control_adapter_not_implemented", "Configured service adapter is not implemented.")
        result = self.transport.run(command, timeout_seconds=timeout_seconds)
        return PlatformActionOutcome(ok=result.completed and result.returncode == 0)

    def service_has_state(self, target: str, state: str, *, timeout_seconds: int) -> bool:
        command = self._service_status_command(target)
        if not command:
            return False
        result = self.transport.run(command, timeout_seconds=timeout_seconds)
        running = (
            result.completed and result.returncode == 0 and result.stdout.strip().lower() == "true"
            if self.definition.service_adapter == "docker"
            else result.completed and result.returncode == 0
        )
        return running if state == "started" else not running

    def read_mdstat(self, *, timeout_seconds: int) -> PlatformObservation:
        return self._observe(["cat", "/proc/mdstat"], timeout_seconds)

    def inspect_mount(self, path: str, *, target_only: bool = False, timeout_seconds: int) -> PlatformObservation:
        fields = "TARGET" if target_only else "SOURCE,TARGET,OPTIONS"
        return self._observe(["findmnt", "-rn", "-o", fields, path], timeout_seconds)

    def probe_write(self, path: str, *, timeout_seconds: int) -> bool:
        script = 'probe="$1/.oracle-readiness-$$"; trap \'rm -f "$probe"\' EXIT HUP INT TERM; (umask 077 && printf "oracle-readiness\\n" > "$probe") && test -s "$probe" && rm -f "$probe"'
        command = ["sh", "-c", script, "oracle-readiness", path]
        return self._succeeded(command, timeout_seconds)

    def unmount(self, path: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("umount", path), timeout_seconds)

    def restart_mount_service(self, target: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("systemctl", "restart", target), timeout_seconds)

    def remount_read_write(self, path: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("mount", "-o", "remount,rw", path), timeout_seconds)

    def flush_writes(self, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("sync"), timeout_seconds)

    def stop_raid(self, array_id: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("mdadm", "--stop", f"/dev/{array_id}"), timeout_seconds)

    def assemble_raid(self, array_id: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("mdadm", "--assemble", f"/dev/{array_id}"), timeout_seconds)

    def mount(self, path: str, *, timeout_seconds: int) -> bool:
        return self._succeeded(_sudo("mount", path), timeout_seconds)

    def _restart_host(self, operation: str, timeout_seconds: int) -> PlatformActionOutcome:
        if operation != "restart_host":
            return _failure("service_control_not_implemented", "Approved host control execution is not implemented.")
        if self.definition.transport == "local":
            if self.definition.platform != "linux":
                return _failure("service_control_transport_not_implemented", "Local host restart is not implemented.")
            return self._schedule_deferred_host_restart()
        command = _sudo("reboot") if self.definition.platform == "linux" else ["shutdown.exe", "/r", "/t", "0", "/f"]
        result = self.transport.run(command, timeout_seconds=max(5, timeout_seconds))
        if result.configuration_error:
            return _failure("service_control_transport_not_configured", "Service-control transport is not fully configured.")
        if result.timed_out or (result.completed and result.returncode in {0, 255}):
            return PlatformActionOutcome(True, "restart_sent", detail="Host restart request was sent.", platform=self.definition.platform)
        return _failure("service_control_command_failed", "Service-control command could not be sent.")

    def _service_restart_command(self, target: str) -> list[str]:
        adapter = self.definition.service_adapter
        if adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
            return _sudo("systemctl", "restart", target)
        if adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
            return ["docker", "restart", target]
        if adapter == "windows_scheduled_task" and _WINDOWS_TASK_PATTERN.fullmatch(target):
            if self.definition.restart_mode == "restart_edge_kiosk":
                script = (
                    f"$task=Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                    f"if ($task.State -eq 'Running') {{ Stop-ScheduledTask -TaskName '{target}' -ErrorAction Stop; Start-Sleep -Seconds 2 }}; "
                    "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force; "
                    f"schtasks.exe /Run /TN '{target}' /I | Out-Null; if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}"
                )
            else:
                script = (
                    f"$task=Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                    f"if ($task.State -eq 'Running') {{ Stop-ScheduledTask -TaskName '{target}' -ErrorAction Stop; Start-Sleep -Seconds 2 }}; "
                    f"Start-ScheduledTask -TaskName '{target}' -ErrorAction Stop"
                )
            return [_powershell(script)]
        return []

    def _service_state_command(self, target: str, state: str) -> list[str]:
        verb = "start" if state == "started" else "stop"
        if self.definition.service_adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
            return _sudo("systemctl", verb, target)
        if self.definition.service_adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
            return ["docker", verb, target]
        return []

    def _service_status_command(self, target: str) -> list[str]:
        adapter = self.definition.service_adapter
        if adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
            return _sudo("systemctl", "is-active", "--quiet", target)
        if adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
            return ["docker", "inspect", "-f", "{{.State.Running}}", target]
        if adapter == "windows_scheduled_task" and _WINDOWS_TASK_PATTERN.fullmatch(target):
            if self.definition.verification_mode == "edge_running":
                script = "if (-not (Get-Process msedge -ErrorAction SilentlyContinue)) { exit 1 }"
            else:
                script = f"$state=(Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop).State; if ($state -ne 'Running') {{ exit 1 }}"
            return [_powershell(script)]
        return []

    def _observe(self, command: Sequence[str], timeout_seconds: int) -> PlatformObservation:
        result = self.transport.run(command, timeout_seconds=timeout_seconds)
        return PlatformObservation(result.completed and result.returncode == 0, "available" if result.returncode == 0 else "failed", result.stdout)

    def _succeeded(self, command: Sequence[str], timeout_seconds: int) -> bool:
        result = self.transport.run(command, timeout_seconds=timeout_seconds)
        return result.completed and result.returncode == 0

    def _restore_companions(self, targets: list[str], timeout_seconds: int) -> bool:
        return all(self.set_service_state(target, "started", timeout_seconds=timeout_seconds).ok for target in reversed(targets))

    def _schedule_deferred_service_restart(self) -> PlatformActionOutcome:
        target = str(self.definition.service_target or "")
        delay = int(self.definition.deferred_delay_seconds or 2)
        code = "import subprocess,sys,time; time.sleep(int(sys.argv[1])); subprocess.run(['sudo','-n','systemctl','restart',sys.argv[2]],check=False)"
        if not _spawn([sys.executable, "-c", code, str(delay), target]):
            return _failure("service_control_command_failed", "Service-control command could not be scheduled.")
        return PlatformActionOutcome(True, "scheduled", deferred=True)

    def _schedule_deferred_host_restart(self) -> PlatformActionOutcome:
        code = "import subprocess,time; time.sleep(3); subprocess.run(['sudo','-n','reboot'],check=False)"
        if not _spawn([sys.executable, "-c", code]):
            return _failure("service_control_command_failed", "Host restart request could not be scheduled.")
        return PlatformActionOutcome(True, "scheduled", platform="linux", deferred=True, detail="Local host restart was scheduled and will run after the response returns.")


class RouterPlatformAdapter:
    def __init__(self, definition: RouterControlAdapter, credential: str) -> None:
        self.definition = definition
        self.credential_available = bool(str(credential or "").strip())
        self.transport = SshPlatformTransport(address=definition.address, user=definition.user, credential=credential)

    def restart(self, operation: str) -> PlatformActionOutcome:
        if operation != "restart_router" or self.definition.mechanism != "ssh_reboot":
            return _failure("router_control_not_implemented", "Approved router control adapter is not implemented.")
        if not self.credential_available:
            return _failure("router_control_credentials_missing", "Router-control SSH credential is unavailable.")
        result = self.transport.run(["reboot"], timeout_seconds=15)
        if result.timed_out or (result.completed and result.returncode in {0, 255}):
            return PlatformActionOutcome(True, "restart_sent", detail="Router restart request was sent.")
        return _failure("router_control_command_failed", "Router-control restart request could not be sent.")


def check_json_health(url: str, *, timeout_seconds: int) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    try:
        with request.urlopen(url, timeout=max(2, min(15, int(timeout_seconds)))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is True and payload.get("has_errors") is not True


def mount_options(output: str) -> list[str]:
    fields = output.split()
    return [item.strip() for item in fields[-1].split(",") if item.strip()] if len(fields) >= 3 else []


def mount_target(output: str) -> str:
    fields = output.split()
    return fields[-2] if len(fields) >= 3 else ""


def raid_array_healthy(mdstat: str, *, array_id: str) -> bool:
    lines = mdstat.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith(f"{array_id} :"):
            continue
        header = line.strip()
        detail = " ".join(item.strip() for item in lines[index + 1:index + 3])
        state = re.search(r"\[([U_]+)\]", detail)
        return " active " in f" {header} " and bool(state) and "_" not in state.group(1)
    return False


def _transport(definition: ServiceControlAdapter, credential: str | None) -> PlatformTransport:
    if definition.transport == "local":
        return LocalPlatformTransport()
    return SshPlatformTransport(address=str(definition.address or ""), user=str(definition.user or ""), credential=str(credential or ""))


def _sudo(*command: str) -> list[str]:
    return ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", *command]


def _powershell(script: str) -> str:
    return f'powershell.exe -NoProfile -NonInteractive -Command "{script.replace(chr(34), chr(92) + chr(34))}"'


def _spawn(argv: Sequence[str]) -> bool:
    try:
        subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _failure(error_code: str, detail: str) -> PlatformActionOutcome:
    return PlatformActionOutcome(False, error=error_code, detail=detail)
