from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .models import SyncResult, WakeCaptureConfig, WakeCaptureUploadConfig
from .storage import iter_pending_files, pending_root, relative_pending_path, synced_root


_KNOWN_HOSTS_ENVIRONMENT = "ORACLE_SSH_KNOWN_HOSTS_FILE"
_SSH_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
_SSH_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _strict_ssh_options() -> list[str]:
    configured = str(os.environ.get(_KNOWN_HOSTS_ENVIRONMENT) or "").strip()
    if not configured:
        raise ValueError(
            f"{_KNOWN_HOSTS_ENVIRONMENT} must identify the validated household SSH known-hosts file"
        )
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise ValueError(f"{_KNOWN_HOSTS_ENVIRONMENT} must be an absolute path")
    try:
        known_hosts = candidate.resolve(strict=True)
        metadata = known_hosts.stat()
    except (OSError, RuntimeError) as exc:
        raise ValueError("The configured SSH known-hosts file is unavailable") from exc
    if not known_hosts.is_file() or metadata.st_size == 0:
        raise ValueError("The configured SSH known-hosts file must be a nonempty regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("The configured SSH known-hosts file must not be group- or world-writable")
    if not os.access(known_hosts, os.R_OK):
        raise ValueError("The configured SSH known-hosts file is not readable")
    return [
        "-F", "/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
    ]


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def _prune_old_synced_files(
    config: WakeCaptureConfig | WakeCaptureUploadConfig,
) -> None:
    if config.synced_local_retention_days <= 0:
        return
    cutoff = time.time() - (config.synced_local_retention_days * 86400)
    root = synced_root(config.local_storage_path)
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
    _prune_empty_dirs(root)


def _sync_to_local_path(config: WakeCaptureConfig, files: list[Path]) -> None:
    destination_root = Path(config.server_sync_path)
    for path in files:
        destination = destination_root / relative_pending_path(config.local_storage_path, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _ssh_base_command(config: WakeCaptureConfig) -> list[str]:
    command = ["ssh"]
    if config.sync_ssh_key_path:
        command.extend(["-i", config.sync_ssh_key_path])
    command.extend(["-o", "BatchMode=yes", *_strict_ssh_options()])
    return command


def _remote_target(config: WakeCaptureConfig) -> str:
    host = str(config.sync_host or "").strip()
    user = str(config.sync_user or "").strip()
    if _SSH_HOST_PATTERN.fullmatch(host) is None:
        raise ValueError("Wake-capture SSH host is invalid")
    if user and _SSH_USER_PATTERN.fullmatch(user) is None:
        raise ValueError("Wake-capture SSH user is invalid")
    return f"{user}@{host}" if user else host


def _remote_path(value: str) -> str:
    path = PurePosixPath(str(value or "").strip())
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Wake-capture SSH destination must be an absolute confined path")
    return path.as_posix()


def _ensure_remote_directory(config: WakeCaptureConfig, path: str) -> None:
    subprocess.run(
        [*_ssh_base_command(config), _remote_target(config), shlex.join(["mkdir", "-p", path])],
        check=True,
    )


def _sync_to_remote_host_rsync(config: WakeCaptureConfig) -> None:
    local_pending = pending_root(config.local_storage_path)
    if not local_pending.exists():
        return
    destination_path = _remote_path(config.server_sync_path)
    remote = f"{_remote_target(config)}:{shlex.quote(destination_path + '/')}"
    _ensure_remote_directory(config, destination_path)
    ssh_command = ["ssh"]
    if config.sync_ssh_key_path:
        ssh_command.extend(["-i", config.sync_ssh_key_path])
    ssh_command.extend(["-o", "BatchMode=yes", *_strict_ssh_options()])
    rsync_cmd = ["rsync", "-a", "-e", shlex.join(ssh_command)]
    rsync_cmd.extend([f"{local_pending}/", remote])
    subprocess.run(rsync_cmd, check=True)


def _sync_to_remote_host_scp(config: WakeCaptureConfig, files: list[Path]) -> None:
    local_pending = pending_root(config.local_storage_path)
    source_roots = sorted(
        {
            local_pending / relative_pending_path(config.local_storage_path, path).parts[0]
            for path in files
        }
    )
    scp_base = [
        "scp",
        "-q",
        "-r",
        "-o",
        "BatchMode=yes",
        *_strict_ssh_options(),
    ]
    if config.sync_ssh_key_path:
        scp_base.extend(["-i", config.sync_ssh_key_path])
    remote_target = _remote_target(config)
    destination_path = _remote_path(config.server_sync_path)
    subprocess.run(
        [
            *scp_base,
            *(str(path) for path in source_roots),
            f"{remote_target}:{shlex.quote(destination_path + '/')}",
        ],
        check=True,
    )


def _resolve_remote_transport(config: WakeCaptureConfig) -> str:
    configured = str(config.sync_transport or "auto").strip().lower()
    if configured not in {"auto", "rsync", "scp"}:
        raise ValueError(f"Unsupported wake capture sync transport: {configured}")
    if configured == "auto":
        return "rsync" if shutil.which("rsync") else "scp"
    return configured


def _sync_to_remote_host(config: WakeCaptureConfig, files: list[Path]) -> None:
    transport = _resolve_remote_transport(config)
    if transport == "rsync":
        _sync_to_remote_host_rsync(config)
        return
    _sync_to_remote_host_scp(config, files)


def sync_pending_captures(*, config: WakeCaptureConfig, logger: logging.Logger) -> SyncResult:
    result = SyncResult()
    if not (config.enabled and config.sync_enabled):
        return result
    files = iter_pending_files(config.local_storage_path)
    if not files:
        _prune_old_synced_files(config)
        return result
    try:
        if config.sync_host:
            _sync_to_remote_host(config, files)
        else:
            _sync_to_local_path(config, files)
    except Exception as exc:
        logger.warning("Wake capture sync failed: %s", exc)
        return result

    result.synced_files = len(files)
    if config.delete_local_after_sync:
        for path in files:
            path.unlink(missing_ok=True)
            result.deleted_local_files += 1
    else:
        sync_root = synced_root(config.local_storage_path)
        for path in files:
            destination = sync_root / relative_pending_path(config.local_storage_path, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            result.retained_local_files += 1
    _prune_empty_dirs(pending_root(config.local_storage_path))
    _prune_old_synced_files(config)
    return result


OpenWakeCaptureUrl = Callable[..., Any]
MAX_UPLOAD_RESPONSE_BYTES = 64 * 1024
MAX_UPLOAD_METADATA_BYTES = 64 * 1024
MAX_UPLOAD_AUDIO_BYTES = 16 * 1024 * 1024
_CAPTURE_ID = re.compile(r"^[0-9a-f]{64}$")


def sync_pending_captures_http(
    *,
    config: WakeCaptureUploadConfig,
    logger: logging.Logger,
    open_url: OpenWakeCaptureUrl = urlrequest.urlopen,
) -> SyncResult:
    result = SyncResult()
    if not config.enabled:
        return result
    pairs = _pending_capture_pairs(config.local_storage_path)
    if not pairs:
        _prune_old_synced_files(config)
        return result
    endpoint = _wake_capture_endpoint(config.brain_base_url, config.satellite_id)
    for wav_path, metadata_path in pairs:
        try:
            _upload_capture_pair(
                endpoint=endpoint,
                credential=config.brain_credential,
                expected_source_id=config.source_id,
                wav_path=wav_path,
                metadata_path=metadata_path,
                open_url=open_url,
            )
        except Exception as exc:
            logger.warning("Wake capture upload failed: %s", exc)
            break
        result.synced_files += 2
        if config.delete_local_after_sync:
            wav_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            result.deleted_local_files += 2
        else:
            for path in (wav_path, metadata_path):
                destination = synced_root(config.local_storage_path) / relative_pending_path(
                    config.local_storage_path,
                    path,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(destination))
                result.retained_local_files += 1
    _prune_empty_dirs(pending_root(config.local_storage_path))
    _prune_old_synced_files(config)
    return result


def _pending_capture_pairs(local_storage_path: Path) -> list[tuple[Path, Path]]:
    files = iter_pending_files(local_storage_path)
    by_stem: dict[Path, dict[str, Path]] = {}
    for path in files:
        if path.suffix not in {".wav", ".json"}:
            continue
        by_stem.setdefault(path.with_suffix(""), {})[path.suffix] = path
    return [
        (members[".wav"], members[".json"])
        for _, members in sorted(by_stem.items())
        if set(members) == {".wav", ".json"}
    ]


def _upload_capture_pair(
    *,
    endpoint: str,
    credential: str,
    expected_source_id: str,
    wav_path: Path,
    metadata_path: Path,
    open_url: OpenWakeCaptureUrl,
) -> None:
    if metadata_path.stat().st_size > MAX_UPLOAD_METADATA_BYTES:
        raise RuntimeError("Wake-capture metadata exceeds the supported size.")
    if wav_path.stat().st_size > MAX_UPLOAD_AUDIO_BYTES:
        raise RuntimeError("Wake-capture audio exceeds the supported size.")
    metadata = metadata_path.read_bytes()
    audio = wav_path.read_bytes()
    try:
        metadata_value = json.loads(metadata)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Wake-capture metadata is invalid.") from exc
    if not isinstance(metadata_value, dict) or metadata_value.get("source_id") != expected_source_id:
        raise RuntimeError("Wake-capture source does not match the selected projection.")
    boundary = "oracle-wake-capture-" + secrets.token_hex(16)
    request = urlrequest.Request(
        endpoint,
        data=_multipart_body(boundary, metadata, audio),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {credential}",
            "Cache-Control": "no-cache",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with open_url(request, timeout=15.0) as response:
            content_type = str(response.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
            cache_control = str(response.headers.get("Cache-Control") or "").lower()
            response_body = response.read(MAX_UPLOAD_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as exc:
        raise RuntimeError(f"Wake-capture service rejected the request with HTTP {exc.code}.") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Wake-capture service is unavailable.") from exc
    if (
        content_type != "application/json"
        or "no-store" not in {item.strip() for item in cache_control.split(",")}
        or len(response_body) > MAX_UPLOAD_RESPONSE_BYTES
    ):
        raise RuntimeError("Wake-capture service returned an invalid response boundary.")
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Wake-capture service returned an invalid response.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or not isinstance(payload.get("capture_id"), str)
        or _CAPTURE_ID.fullmatch(payload["capture_id"]) is None
    ):
        raise RuntimeError("Wake-capture service returned an invalid response.")


def _multipart_body(boundary: str, metadata: bytes, audio: bytes) -> bytes:
    marker = boundary.encode("ascii")
    return b"".join(
        (
            b"--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="metadata"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            metadata,
            b"\r\n--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="audio"; filename="capture.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            b"\r\n--" + marker + b"--\r\n",
        )
    )


def _wake_capture_endpoint(base_url: str, satellite_id: str) -> str:
    parsed = urlparse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Selected Brain endpoint is invalid.")
    path = (
        parsed.path.rstrip("/")
        + "/api/satellite/wake-captures/"
        + urlparse.quote(satellite_id, safe="")
    )
    return urlparse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
