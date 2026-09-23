#!/usr/bin/env python3
"""Operator-initiated To Do device consent into Oracle canonical secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib import error, parse, request


_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
_SECRET = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SCOPE = "Tasks.ReadWrite offline_access"


def _post(url: str, fields: dict[str, str], timeout: int) -> dict:
    req = request.Request(url, data=parse.urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read(65537)
    except error.HTTPError as exc:
        body = exc.read(65537)
    if len(body) > 65536:
        raise RuntimeError("Microsoft consent response exceeded the bounded limit.")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Microsoft consent returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Microsoft consent returned an invalid response.")
    return value


def _consent(client_id: str, tenant: str) -> str:
    root = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    device = _post(root + "/devicecode", {"client_id": client_id, "scope": _SCOPE}, 15)
    code, user_code, uri = device.get("device_code"), device.get("user_code"), device.get("verification_uri")
    if not all(isinstance(value, str) and value for value in (code, user_code, uri)):
        raise RuntimeError("Microsoft did not issue a usable device authorization code.")
    parsed_uri = parse.urlsplit(uri)
    if parsed_uri.scheme != "https" or parsed_uri.hostname not in {"microsoft.com", "www.microsoft.com", "login.microsoftonline.com"}:
        raise RuntimeError("Microsoft supplied an unexpected verification origin.")
    print(f"Open {uri} and enter code {user_code}. No token will be printed or saved locally.", flush=True)
    deadline = time.monotonic() + min(int(device.get("expires_in", 900)), 900)
    interval = max(5, min(int(device.get("interval", 5)), 30))
    while time.monotonic() < deadline:
        time.sleep(interval)
        response = _post(root + "/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": client_id, "device_code": code}, 15)
        token = response.get("refresh_token")
        if isinstance(token, str) and token:
            return token
        status = response.get("error")
        if status == "authorization_pending":
            continue
        if status == "slow_down":
            interval = min(interval + 5, 30)
            continue
        raise RuntimeError("Microsoft device consent did not complete; check account, app registration, and delegated scope.")
    raise RuntimeError("Microsoft device consent expired before completion.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consent to Microsoft To Do and install its refresh credential through Oracle's canonical secret transaction")
    parser.add_argument("--tenant", required=True, help="consumers, organizations, or a tenant UUID")
    parser.add_argument("--client-id", required=True, help="registered public-client application UUID")
    parser.add_argument("--logical-id", required=True, help="canonical secret logical ID")
    parser.add_argument("--expected-secret-generation", required=True)
    parser.add_argument("--socket", required=True, type=Path, help="protected Oracle host-local configuration socket")
    parser.add_argument("--secret-operation", choices=("create_secret", "replace_secret", "rotate_secret"), default="create_secret")
    args = parser.parse_args(argv)
    if args.tenant not in {"consumers", "organizations"} and not _UUID.fullmatch(args.tenant):
        parser.error("tenant must be consumers, organizations, or a tenant UUID")
    if not _UUID.fullmatch(args.client_id) or not _SECRET.fullmatch(args.logical_id):
        parser.error("client ID or logical secret ID has an invalid shape")
    if not args.socket.is_absolute():
        parser.error("configuration socket must be an absolute path")
    try:
        refresh_token = _consent(args.client_id, args.tenant)
        cli = Path(__file__).with_name("oracle-config.py")
        result = subprocess.run(
            [sys.executable, "-B", str(cli), "--socket", str(args.socket), "secret", args.secret_operation, args.logical_id, "--expected-secret-generation", args.expected_secret_generation, "--value-stdin"],
            input=refresh_token + "\n", capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Oracle canonical secret transaction failed; no alternate token store was written.")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Microsoft To Do credential installed through the canonical secret transaction; verify complete activation before enabling the provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
