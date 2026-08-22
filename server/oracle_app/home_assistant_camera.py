from __future__ import annotations

from dataclasses import dataclass
import time
from urllib import parse
from urllib import error, request


class HomeAssistantSnapshotError(RuntimeError):
    """Raised when a Home Assistant still snapshot cannot be fetched."""


@dataclass(frozen=True)
class HomeAssistantSnapshotMetadata:
    available: bool
    content_type: str | None = None
    content_length: int | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class HomeAssistantSnapshot:
    content: bytes
    content_type: str
    last_modified: str | None = None


def fetch_snapshot_metadata(
    *,
    base_url: str,
    token: str,
    snapshot_path: str,
    snapshot_root: str = "/local/snapshots",
    timeout_seconds: float = 3.0,
) -> HomeAssistantSnapshotMetadata:
    req = _build_snapshot_request(
        base_url=base_url,
        token=token,
        snapshot_path=snapshot_path,
        snapshot_root=snapshot_root,
        method="HEAD",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            content_length = _parse_content_length(response.headers.get("Content-Length"))
            return HomeAssistantSnapshotMetadata(
                available=True,
                content_type=response.headers.get("Content-Type"),
                content_length=content_length,
                last_modified=response.headers.get("Last-Modified"),
            )
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        return HomeAssistantSnapshotMetadata(available=False)


def fetch_snapshot(
    *,
    base_url: str,
    token: str,
    snapshot_path: str,
    snapshot_root: str = "/local/snapshots",
    timeout_seconds: float = 8.0,
) -> HomeAssistantSnapshot:
    req = _build_snapshot_request(
        base_url=base_url,
        token=token,
        snapshot_path=snapshot_path,
        snapshot_root=snapshot_root,
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type") or "image/jpeg"
            return HomeAssistantSnapshot(
                content=content,
                content_type=content_type,
                last_modified=response.headers.get("Last-Modified"),
            )
    except error.HTTPError as exc:
        raise HomeAssistantSnapshotError(f"Home Assistant snapshot returned HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise HomeAssistantSnapshotError(f"Home Assistant snapshot is unavailable: {exc}") from exc


def _build_snapshot_request(
    *,
    base_url: str,
    token: str,
    snapshot_path: str,
    snapshot_root: str,
    method: str,
) -> request.Request:
    normalized_path = _normalize_snapshot_path(snapshot_path, snapshot_root=snapshot_root)
    return request.Request(
        _build_snapshot_url(base_url=base_url, snapshot_path=normalized_path),
        headers={"Authorization": f"Bearer {token}"} if token else {},
        method=method,
    )


def _normalize_snapshot_path(snapshot_path: str, *, snapshot_root: str = "/local/snapshots") -> str:
    normalized = str(snapshot_path or "").strip()
    root = str(snapshot_root or "").rstrip("/")
    if not root.startswith("/") or not normalized.startswith(f"{root}/"):
        raise HomeAssistantSnapshotError("Unsupported Home Assistant camera snapshot path.")
    return normalized


def _build_snapshot_url(*, base_url: str, snapshot_path: str) -> str:
    separator = "&" if "?" in snapshot_path else "?"
    cache_buster = parse.urlencode({"t": str(int(time.time()))})
    return f"{base_url}{snapshot_path}{separator}{cache_buster}"


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
