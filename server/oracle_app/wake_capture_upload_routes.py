from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping
import wave

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from .configuration import (
    GenerationStoreError,
    SatelliteProjectionAuthenticationError,
    SatelliteProjectionResolver,
)
from .configuration.normalization import canonicalize_json
from .satellite_projection_routes import _bearer_token


logger = logging.getLogger("oracle-brain.api")

WAKE_CAPTURE_ARCHIVE_ROOT_ENV = "ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT"
MAX_WAKE_CAPTURE_METADATA_BYTES = 64 * 1024
MAX_WAKE_CAPTURE_AUDIO_BYTES = 16 * 1024 * 1024
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_STATE_KEY = "wake_capture_upload_service"
_METADATA_FIELDS = {
    "event_type",
    "timestamp",
    "source_id",
    "score",
    "playback_active",
    "ducking_triggered",
    "timestamp_iso",
    "sample_rate",
    "channels",
    "sample_width_bytes",
    "format",
}


class WakeCaptureUploadError(ValueError):
    pass


class WakeCaptureArchiveError(OSError):
    pass


class WakeCaptureUploadService:
    def __init__(self, resolver: SatelliteProjectionResolver, archive_root: Path) -> None:
        self.resolver = resolver
        self.archive_root = _archive_root(archive_root)

    def authenticate(self, satellite_id: str, credential: str):
        return self.resolver.resolve(satellite_id, credential)

    def persist(
        self,
        satellite_id: str,
        source_id: str,
        metadata_bytes: bytes,
        audio_bytes: bytes,
    ) -> str:
        metadata = _validate_metadata(metadata_bytes, source_id=source_id)
        _validate_wav(audio_bytes, metadata)
        canonical_metadata = canonicalize_json(metadata)
        digest = hashlib.sha256(canonical_metadata + b"\0" + audio_bytes).hexdigest()
        captured_at = datetime.fromtimestamp(float(metadata["timestamp"]), tz=UTC)
        directory = (
            self.archive_root
            / satellite_id
            / captured_at.strftime("%Y-%m-%d")
            / str(metadata["event_type"])
        )
        directory.mkdir(parents=True, exist_ok=True)
        wav_path = directory / f"{digest}.wav"
        metadata_path = directory / f"{digest}.json"
        _persist_capture_pair(
            wav_path=wav_path,
            metadata_path=metadata_path,
            audio_bytes=audio_bytes,
            metadata_bytes=canonical_metadata,
        )
        return digest


def configure_wake_capture_upload_routes(
    app: FastAPI,
    resolver: SatelliteProjectionResolver | None,
    archive_root: Path | None,
) -> None:
    service = None
    if resolver is not None and archive_root is not None:
        service = WakeCaptureUploadService(resolver, archive_root)
    setattr(app.state, _STATE_KEY, service)


def wake_capture_archive_root_from_environment(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    values = os.environ if environment is None else environment
    value = values.get(WAKE_CAPTURE_ARCHIVE_ROOT_ENV)
    if value is None:
        return None
    if not str(value).strip():
        raise WakeCaptureUploadError("Wake-capture archive root cannot be empty.")
    return _archive_root(Path(str(value)))


async def satellite_wake_capture_upload(
    satellite_id: str,
    request: Request,
    metadata: str = Form(...),
    audio: UploadFile = File(...),
) -> JSONResponse:
    credential = _bearer_token(request.headers.get("Authorization"))
    if not credential:
        raise _authentication_error()
    service = getattr(request.app.state, _STATE_KEY, None)
    if service is None:
        raise _unavailable_error()
    try:
        resolved = service.authenticate(satellite_id, credential)
    except SatelliteProjectionAuthenticationError as exc:
        raise _authentication_error() from exc
    except (GenerationStoreError, OSError) as exc:
        logger.error(
            "wake_capture_authentication_unavailable satellite_id=%s error_type=%s",
            satellite_id,
            type(exc).__name__,
        )
        raise _unavailable_error() from exc

    encoded_metadata = metadata.encode("utf-8")
    if len(encoded_metadata) > MAX_WAKE_CAPTURE_METADATA_BYTES:
        raise _invalid_upload()
    audio_bytes = await audio.read(MAX_WAKE_CAPTURE_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_WAKE_CAPTURE_AUDIO_BYTES:
        raise _invalid_upload()
    source_id = resolved.installed.projection.projection.source_id
    try:
        capture_id = service.persist(
            satellite_id,
            source_id,
            encoded_metadata,
            audio_bytes,
        )
    except WakeCaptureUploadError as exc:
        raise _invalid_upload() from exc
    except OSError as exc:
        logger.error(
            "wake_capture_persist_failed satellite_id=%s error_type=%s",
            satellite_id,
            type(exc).__name__,
        )
        raise _unavailable_error() from exc
    return JSONResponse(
        {"ok": True, "capture_id": capture_id},
        headers=_NO_STORE_HEADERS,
    )


def register_wake_capture_upload_routes(app: FastAPI) -> None:
    app.post("/api/satellite/wake-captures/{satellite_id}")(
        satellite_wake_capture_upload
    )


def _archive_root(path: Path) -> Path:
    if not path.is_absolute():
        raise WakeCaptureUploadError("Wake-capture archive root must be absolute.")
    target = path.expanduser().resolve(strict=False)
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise WakeCaptureUploadError("Wake-capture archive root must be a directory.")
    return target


def _validate_metadata(encoded: bytes, *, source_id: str) -> dict[str, object]:
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WakeCaptureUploadError("Wake-capture metadata is invalid.") from exc
    if not isinstance(value, dict) or set(value) != _METADATA_FIELDS:
        raise WakeCaptureUploadError("Wake-capture metadata is invalid.")
    if value["event_type"] not in {"activation", "near_threshold"}:
        raise WakeCaptureUploadError("Wake-capture event type is invalid.")
    if value["source_id"] != source_id:
        raise WakeCaptureUploadError("Wake-capture source identity is invalid.")
    timestamp = value["timestamp"]
    score = value["score"]
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(float(timestamp))
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 1.0
    ):
        raise WakeCaptureUploadError("Wake-capture numeric metadata is invalid.")
    try:
        captured_at = datetime.fromtimestamp(float(timestamp), tz=UTC)
        timestamp_iso = datetime.fromisoformat(str(value["timestamp_iso"]))
    except (ValueError, OverflowError) as exc:
        raise WakeCaptureUploadError("Wake-capture timestamp is invalid.") from exc
    if timestamp_iso.tzinfo is None or abs(timestamp_iso.timestamp() - captured_at.timestamp()) > 0.001:
        raise WakeCaptureUploadError("Wake-capture timestamp is invalid.")
    if not isinstance(value["playback_active"], bool):
        raise WakeCaptureUploadError("Wake-capture playback state is invalid.")
    if value["ducking_triggered"] is not None and not isinstance(value["ducking_triggered"], bool):
        raise WakeCaptureUploadError("Wake-capture ducking state is invalid.")
    if (
        isinstance(value["sample_rate"], bool)
        or not isinstance(value["sample_rate"], int)
        or not 8_000 <= value["sample_rate"] <= 96_000
        or value["channels"] != 1
        or value["sample_width_bytes"] != 2
        or value["format"] != "wav_pcm_s16le"
    ):
        raise WakeCaptureUploadError("Wake-capture audio metadata is invalid.")
    return value


def _validate_wav(encoded: bytes, metadata: dict[str, object]) -> None:
    if not encoded:
        raise WakeCaptureUploadError("Wake-capture audio is invalid.")
    try:
        with wave.open(io.BytesIO(encoded), "rb") as handle:
            valid = (
                handle.getcomptype() == "NONE"
                and handle.getnchannels() == metadata["channels"]
                and handle.getsampwidth() == metadata["sample_width_bytes"]
                and handle.getframerate() == metadata["sample_rate"]
                and handle.getnframes() > 0
            )
    except (EOFError, wave.Error) as exc:
        raise WakeCaptureUploadError("Wake-capture audio is invalid.") from exc
    if not valid:
        raise WakeCaptureUploadError("Wake-capture audio is invalid.")


def _persist_capture_pair(
    *,
    wav_path: Path,
    metadata_path: Path,
    audio_bytes: bytes,
    metadata_bytes: bytes,
) -> None:
    if metadata_path.exists():
        if not wav_path.is_file() or wav_path.read_bytes() != audio_bytes or metadata_path.read_bytes() != metadata_bytes:
            raise WakeCaptureArchiveError("Wake-capture archive state is inconsistent.")
        return
    if wav_path.exists() and (not wav_path.is_file() or wav_path.read_bytes() != audio_bytes):
        raise WakeCaptureArchiveError("Wake-capture archive state is inconsistent.")
    if not wav_path.exists():
        _atomic_write_new(wav_path, audio_bytes)
    _atomic_write_new(metadata_path, metadata_bytes)
    _fsync_directory(wav_path.parent)


def _atomic_write_new(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise WakeCaptureArchiveError("Wake-capture archive state changed during persistence.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Satellite wake-capture authentication failed.",
        headers={"WWW-Authenticate": "Bearer", **_NO_STORE_HEADERS},
    )


def _invalid_upload() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Satellite wake-capture upload is invalid.",
        headers=_NO_STORE_HEADERS,
    )


def _unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Satellite wake-capture archive is unavailable.",
        headers=_NO_STORE_HEADERS,
    )
