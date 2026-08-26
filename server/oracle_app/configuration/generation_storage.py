from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from typing import Any, Protocol


class GenerationStoreError(ValueError):
    pass


class StoreLineageConflict(GenerationStoreError):
    pass


class GenerationIntegrityError(GenerationStoreError):
    pass


class GenerationCompatibilityError(GenerationStoreError):
    pass


class SecretGenerationRevokedError(GenerationStoreError):
    pass


class SecretGenerationPrunedError(GenerationStoreError):
    pass


class GenerationStoreLike(Protocol):
    root: Path
    configuration_directory_mode: int
    configuration_file_mode: int

    def validate_initialized(self) -> None: ...


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, data: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    temporary = path.parent / f".{path.name}-{secrets.token_hex(16)}.tmp"
    try:
        _write_new(temporary, data, mode=mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is missing or not a regular file.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is unreadable.") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_bytes(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is unreadable or invalid.") from exc


def _require_exact_mapping(value: Any, fields: set[str], *, artifact: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GenerationIntegrityError(f"{artifact} has an invalid manifest shape.")
    return value
