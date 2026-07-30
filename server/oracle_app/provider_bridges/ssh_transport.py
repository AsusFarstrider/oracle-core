from __future__ import annotations

import os
import stat
from pathlib import Path


KNOWN_HOSTS_ENVIRONMENT = "ORACLE_SSH_KNOWN_HOSTS_FILE"


class SshHostVerificationError(RuntimeError):
    """Raised when Oracle cannot establish a strict SSH host-trust boundary."""


def strict_ssh_options(*, connect_timeout_seconds: int | None = None) -> list[str]:
    """Return fail-closed OpenSSH options for an operator-managed trust store."""

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
        "-F",
        "/dev/null",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
    ]
    if connect_timeout_seconds is not None:
        options.extend(["-o", f"ConnectTimeout={max(1, int(connect_timeout_seconds))}"])
    return options
