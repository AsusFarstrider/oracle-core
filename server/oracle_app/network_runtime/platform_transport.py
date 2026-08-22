from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import stat
import subprocess
from typing import Mapping, Protocol, Sequence


KNOWN_HOSTS_ENVIRONMENT = "ORACLE_SSH_KNOWN_HOSTS_FILE"


class SshHostVerificationError(RuntimeError):
    """Raised when Oracle cannot establish its strict SSH trust boundary."""


@dataclass(frozen=True)
class CommandOutcome:
    completed: bool
    returncode: int | None = None
    stdout: str = ""
    timed_out: bool = False
    configuration_error: bool = False


class PlatformTransport(Protocol):
    def run(self, command: Sequence[str], *, timeout_seconds: int) -> CommandOutcome: ...


def strict_ssh_options(*, connect_timeout_seconds: int | None = None) -> list[str]:
    configured = str(os.environ.get(KNOWN_HOSTS_ENVIRONMENT) or "").strip()
    if not configured:
        raise SshHostVerificationError(
            f"{KNOWN_HOSTS_ENVIRONMENT} must identify the validated household SSH known-hosts file"
        )
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise SshHostVerificationError(f"{KNOWN_HOSTS_ENVIRONMENT} must be an absolute path")
    try:
        known_hosts = candidate.resolve(strict=True)
        metadata = known_hosts.stat()
    except (OSError, RuntimeError) as exc:
        raise SshHostVerificationError("The configured SSH known-hosts file is unavailable") from exc
    if not known_hosts.is_file() or metadata.st_size == 0:
        raise SshHostVerificationError("The configured SSH known-hosts file must be a nonempty regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SshHostVerificationError("The configured SSH known-hosts file must not be group- or world-writable")
    if not os.access(known_hosts, os.R_OK):
        raise SshHostVerificationError("The configured SSH known-hosts file is not readable")
    options = [
        "-F", "/dev/null", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
    ]
    if connect_timeout_seconds is not None:
        options.extend(["-o", f"ConnectTimeout={max(1, int(connect_timeout_seconds))}"])
    return options


class LocalPlatformTransport:
    def run(self, command: Sequence[str], *, timeout_seconds: int) -> CommandOutcome:
        argv = list(command)
        if argv[:1] == ["sudo"]:
            command_start = argv.index("--") + 1 if "--" in argv else 4
            argv = ["sudo", "-n", *argv[command_start:]]
        return _run(argv, stdin=None, environment=None, timeout_seconds=timeout_seconds)


class SshPlatformTransport:
    def __init__(self, *, address: str, user: str, credential: str) -> None:
        self.address = address
        self.user = user
        self.credential = credential

    def run(self, command: Sequence[str], *, timeout_seconds: int) -> CommandOutcome:
        if not self.address or not self.user or not self.credential:
            return CommandOutcome(completed=False, configuration_error=True)
        try:
            options = strict_ssh_options(connect_timeout_seconds=8)
        except SshHostVerificationError:
            return CommandOutcome(completed=False, configuration_error=True)
        command_list = list(command)
        environment = os.environ.copy()
        environment["SSHPASS"] = self.credential
        return _run(
            ["sshpass", "-e", "ssh", *options, f"{self.user}@{self.address}", shlex.join(command_list)],
            stdin=f"{self.credential}\n" if command_list[:1] == ["sudo"] else None,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )


def _run(
    argv: Sequence[str],
    *,
    stdin: str | None,
    environment: Mapping[str, str] | None,
    timeout_seconds: int,
) -> CommandOutcome:
    try:
        result = subprocess.run(
            list(argv),
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(3, min(60, int(timeout_seconds))),
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome(completed=False, timed_out=True)
    except (OSError, subprocess.SubprocessError):
        return CommandOutcome(completed=False)
    return CommandOutcome(
        completed=True,
        returncode=result.returncode,
        stdout=str(result.stdout or "") if result.returncode == 0 else "",
    )
