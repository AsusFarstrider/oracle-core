from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from oracle_satellite_projection import (
    SatelliteProjectionLocalStore,
    SatelliteProjectionStoreError,
)
from oracle_satellite_runtime_config import load_runtime_compatibility_file


SYNC_TIMEOUT_SECONDS = 15.0
MAX_PULL_RESPONSE_BYTES = 2 * 1024 * 1024
EXIT_UNCHANGED = 0
EXIT_FAILURE = 1
EXIT_RESTART_REQUIRED = 3


class SatelliteProjectionSyncError(RuntimeError):
    code = "projection_sync_failed"


class SatelliteProjectionBootstrapRequired(SatelliteProjectionSyncError):
    code = "projection_bootstrap_required"


@dataclass(frozen=True)
class SatelliteProjectionSyncResult:
    status: str
    satellite_id: str
    activation_id: str
    previous_activation_id: str
    selection_operation_id: str
    selection_revision: int
    activation_changed: bool
    selection_changed: bool
    restart_required: bool


OpenProjectionUrl = Callable[..., Any]


def sync_satellite_projection(
    store: SatelliteProjectionLocalStore,
    *,
    open_url: OpenProjectionUrl = urlrequest.urlopen,
) -> SatelliteProjectionSyncResult:
    previous = store.load_selected(optional=True)
    if previous is None:
        raise SatelliteProjectionBootstrapRequired(
            "No selected local activation is available; first-contact provisioning is required."
        )
    try:
        brain_client = previous.projection["configuration"]["brain_client"]
        base_url = str(brain_client["base_url"])
        logical_id = str(brain_client["credential_secret"])
        credential = previous.resolve_secret(logical_id)
    except (KeyError, TypeError) as exc:
        raise SatelliteProjectionSyncError("Selected activation lacks its Brain client.") from exc
    if not credential:
        raise SatelliteProjectionSyncError("Selected activation lacks its Brain credential.")

    endpoint = _projection_endpoint(base_url, store.satellite_id)
    selected = _fetch_and_install(
        store,
        endpoint=endpoint,
        credential=credential,
        method="GET",
        open_url=open_url,
    )

    activation_changed = selected.activation_id != previous.activation_id
    selection_changed = (
        selected.selection_operation_id != previous.selection_operation_id
        or selected.selection_revision != previous.selection_revision
    )
    if activation_changed:
        status = "activation_changed"
    elif selection_changed:
        status = "selection_updated"
    else:
        status = "unchanged"
    return SatelliteProjectionSyncResult(
        status=status,
        satellite_id=selected.satellite_id,
        activation_id=selected.activation_id,
        previous_activation_id=previous.activation_id,
        selection_operation_id=selected.selection_operation_id,
        selection_revision=selected.selection_revision,
        activation_changed=activation_changed,
        selection_changed=selection_changed,
        restart_required=selected.restart_required_activation_id is not None,
    )


def provision_satellite_projection(
    store: SatelliteProjectionLocalStore,
    *,
    brain_bootstrap_url: str,
    enrollment_credential: str,
    open_url: OpenProjectionUrl = urlrequest.urlopen,
) -> SatelliteProjectionSyncResult:
    if store.load_selected(optional=True) is not None:
        raise SatelliteProjectionSyncError(
            "First-contact provisioning is invalid after a local activation is selected."
        )
    if not isinstance(enrollment_credential, str) or not enrollment_credential:
        raise SatelliteProjectionSyncError("Enrollment credential is unavailable.")
    selected = _fetch_and_install(
        store,
        endpoint=_enrollment_endpoint(brain_bootstrap_url, store.satellite_id),
        credential=enrollment_credential,
        method="POST",
        open_url=open_url,
    )
    return SatelliteProjectionSyncResult(
        status="provisioned",
        satellite_id=selected.satellite_id,
        activation_id=selected.activation_id,
        previous_activation_id="",
        selection_operation_id=selected.selection_operation_id,
        selection_revision=selected.selection_revision,
        activation_changed=True,
        selection_changed=True,
        restart_required=selected.restart_required_activation_id is not None,
    )


def _fetch_and_install(
    store: SatelliteProjectionLocalStore,
    *,
    endpoint: str,
    credential: str,
    method: str,
    open_url: OpenProjectionUrl,
) -> Any:
    request = urlrequest.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {credential}",
            "Cache-Control": "no-cache",
        },
        method=method,
    )
    try:
        with open_url(request, timeout=SYNC_TIMEOUT_SECONDS) as response:
            content_type = _content_type(response)
            cache_control = str(response.headers.get("Cache-Control") or "").lower()
            body = response.read(MAX_PULL_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as exc:
        raise SatelliteProjectionSyncError(
            f"Projection service rejected the request with HTTP {exc.code}."
        ) from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise SatelliteProjectionSyncError("Projection service is unavailable.") from exc
    if content_type != "application/json" or "no-store" not in {
        item.strip() for item in cache_control.split(",")
    }:
        raise SatelliteProjectionSyncError("Projection service returned an invalid response boundary.")
    if len(body) > MAX_PULL_RESPONSE_BYTES:
        raise SatelliteProjectionSyncError("Projection response exceeds the supported size.")
    try:
        selected = store.install(body)
    except (SatelliteProjectionStoreError, OSError) as exc:
        raise SatelliteProjectionSyncError("Projection response failed local validation.") from exc

    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull and atomically install Oracle satellite configuration.")
    parser.add_argument("--satellite-id", required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--runtime-compatibility", type=Path, required=True)
    parser.add_argument("--brain-bootstrap-url")
    parser.add_argument("--enrollment-credential-file", type=Path)
    parser.add_argument("--mark-restarted", action="store_true")
    args = parser.parse_args(argv)
    try:
        compatibility = load_runtime_compatibility_file(args.runtime_compatibility)
        store = SatelliteProjectionLocalStore(
            args.store_root,
            satellite_id=args.satellite_id,
            runtime_compatibility=compatibility,
        )
        provisioning_values = (
            args.brain_bootstrap_url is not None,
            args.enrollment_credential_file is not None,
        )
        if args.mark_restarted and any(provisioning_values):
            raise SatelliteProjectionSyncError(
                "Restart acknowledgement cannot carry first-contact inputs."
            )
        if args.mark_restarted:
            selected = store.mark_restarted()
            _write_json(
                sys.stdout,
                {
                    "ok": True,
                    "status": "restart_marked",
                    "satellite_id": selected.satellite_id,
                    "activation_id": selected.activation_id,
                    "selection_operation_id": selected.selection_operation_id,
                    "selection_revision": selected.selection_revision,
                    "restart_required": False,
                },
            )
            return EXIT_UNCHANGED
        if any(provisioning_values) and not all(provisioning_values):
            raise SatelliteProjectionSyncError(
                "First-contact provisioning inputs must be supplied together."
            )
        if all(provisioning_values):
            result = provision_satellite_projection(
                store,
                brain_bootstrap_url=args.brain_bootstrap_url,
                enrollment_credential=_load_enrollment_credential(
                    args.enrollment_credential_file
                ),
            )
        else:
            result = sync_satellite_projection(store)
    except SatelliteProjectionSyncError as exc:
        _write_json(sys.stderr, {"ok": False, "code": exc.code, "message": str(exc)})
        return EXIT_FAILURE
    except (OSError, ValueError, json.JSONDecodeError):
        _write_json(
            sys.stderr,
            {
                "ok": False,
                "code": "projection_sync_bootstrap_invalid",
                "message": "Projection sync bootstrap metadata is invalid.",
            },
        )
        return EXIT_FAILURE
    _write_json(sys.stdout, {"ok": True, **asdict(result)})
    return EXIT_RESTART_REQUIRED if result.restart_required else EXIT_UNCHANGED


def _projection_endpoint(base_url: str, satellite_id: str) -> str:
    return _satellite_endpoint(base_url, "projection", satellite_id)


def _enrollment_endpoint(base_url: str, satellite_id: str) -> str:
    return _satellite_endpoint(base_url, "enrollment", satellite_id)


def _satellite_endpoint(base_url: str, operation: str, satellite_id: str) -> str:
    parsed = urlparse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SatelliteProjectionSyncError("Selected Brain endpoint is invalid.")
    path = (
        parsed.path.rstrip("/")
        + f"/api/satellite/{operation}/"
        + urlparse.quote(satellite_id, safe="")
    )
    return urlparse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _load_enrollment_credential(path: Path) -> str:
    if not path.is_absolute():
        raise SatelliteProjectionSyncError(
            "Enrollment credential file path must be absolute."
        )
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError
        if os.name != "nt" and resolved.stat().st_mode & 0o077:
            raise SatelliteProjectionSyncError(
                "Enrollment credential file permissions are too broad."
            )
        encoded = resolved.read_bytes()
    except SatelliteProjectionSyncError:
        raise
    except OSError as exc:
        raise SatelliteProjectionSyncError(
            "Enrollment credential file is unavailable."
        ) from exc
    if len(encoded) > 4096:
        raise SatelliteProjectionSyncError("Enrollment credential file is invalid.")
    try:
        credential = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SatelliteProjectionSyncError(
            "Enrollment credential file is invalid."
        ) from exc
    if credential.endswith("\r\n"):
        credential = credential[:-2]
    elif credential.endswith("\n"):
        credential = credential[:-1]
    if not credential or credential != credential.strip() or any(character.isspace() for character in credential):
        raise SatelliteProjectionSyncError("Enrollment credential file is invalid.")
    return credential


def _content_type(response: Any) -> str:
    getter = getattr(response.headers, "get_content_type", None)
    if callable(getter):
        return str(getter()).lower()
    return str(response.headers.get("Content-Type") or "").partition(";")[0].strip().lower()


def _write_json(stream: Any, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
