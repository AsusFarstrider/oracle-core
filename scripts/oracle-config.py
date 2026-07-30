#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from oracle_app.configuration.host_local import (  # noqa: E402
    HOST_LOCAL_PROTOCOL_FORMAT,
    HostLocalConfigurationClient,
    HostLocalDispatcher,
    HostLocalProtocolError,
    ServicePresenceLock,
    candidate_role_text,
)
from oracle_app.configuration import ConfigurationService, GenerationStore, snapshot_candidate  # noqa: E402
from oracle_app.configuration import SatelliteRuntimeCompatibility  # noqa: E402


MAX_COMPATIBILITY_REPORT_BYTES = 64 * 1024


def _candidate_payload(path: str) -> dict[str, object]:
    roles, revision = candidate_role_text(Path(path))
    return {"roles": roles, "candidate_authored_revision": revision}


def _acknowledgements(args: argparse.Namespace) -> list[str]:
    return list(args.acknowledge or [])


def _secret_value(args: argparse.Namespace) -> str | None:
    if args.secret_operation == "remove_secret":
        return None
    if args.value_stdin:
        value = sys.stdin.readline()
        if value.endswith("\n"):
            value = value[:-1]
        if value.endswith("\r"):
            value = value[:-1]
        return value
    return getpass.getpass("Secret value: ")


def _compatibility_report(path: str) -> dict[str, object]:
    data = Path(path).read_bytes()
    if len(data) > MAX_COMPATIBILITY_REPORT_BYTES:
        raise HostLocalProtocolError("Runtime compatibility report exceeds the CLI limit.")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostLocalProtocolError("Runtime compatibility report is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HostLocalProtocolError("Runtime compatibility report must be a JSON object.")
    return payload


def build_request(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "status":
        return {"operation": "status"}
    if args.command == "review":
        return {"operation": "review_candidate", **_candidate_payload(args.candidate)}
    if args.command == "activate":
        return {
            "operation": "activate_candidate",
            **_candidate_payload(args.candidate),
            "expected_secret_generation_id": args.expected_secret_generation,
            "acknowledgements": _acknowledgements(args),
        }
    if args.command == "apply":
        return {
            "operation": "replace_authored_candidate",
            **_candidate_payload(args.candidate),
            "expected_authored_revision": args.expected_authored_revision,
            "expected_secret_generation_id": args.expected_secret_generation,
            "acknowledgements": _acknowledgements(args),
        }
    if args.command == "rollback":
        return {
            "operation": "rollback",
            "config_generation_id": args.config_generation,
            "expected_secret_generation_id": args.expected_secret_generation,
            "acknowledgements": _acknowledgements(args),
        }
    if args.command == "secret":
        return {
            "operation": "mutate_secret",
            "secret_operation": args.secret_operation,
            "logical_id": args.logical_id,
            "value": _secret_value(args),
            "expected_secret_generation_id": args.expected_secret_generation,
        }
    if args.command == "recover":
        return {"operation": "recover"}
    if args.command == "cutover":
        return {"operation": "require_canonical_runtime", "acknowledge_one_way": args.acknowledge_one_way}
    if args.command == "compatibility" and args.compatibility_command == "accept":
        return {
            "operation": "accept_satellite_runtime_compatibility",
            "satellite_id": args.satellite_id,
            "compatibility_report": _compatibility_report(args.report),
        }
    raise HostLocalProtocolError("Unsupported CLI command.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Oracle V2 host-local configuration client")
    transport = root.add_mutually_exclusive_group(required=True)
    transport.add_argument("--socket", help="Bootstrap Unix-domain socket path")
    transport.add_argument("--offline-store", help="Installed store path for explicit service-stopped operation")
    root.add_argument("--authoring-root", help="Bootstrap authoring root for offline status, apply, secret, and recovery")
    root.add_argument(
        "--authoring-mode",
        choices=("managed_writable", "external_read_only"),
        default="external_read_only",
        help="Bootstrap authoring mode for offline operation",
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show sanitized selected-generation and authoring status")

    review = commands.add_parser("review", help="Validate and diff a complete candidate directory")
    review.add_argument("--candidate", required=True)

    activate = commands.add_parser("activate", help="Activate a complete candidate without editing the authored workspace")
    activate.add_argument("--candidate", required=True)
    activate.add_argument("--expected-secret-generation")
    activate.add_argument("--acknowledge", action="append", default=[])

    apply = commands.add_parser("apply", help="Replace managed authored YAML and activate it atomically")
    apply.add_argument("--candidate", required=True)
    apply.add_argument("--expected-authored-revision", required=True)
    apply.add_argument("--expected-secret-generation", required=True)
    apply.add_argument("--acknowledge", action="append", default=[])

    rollback = commands.add_parser("rollback", help="Select a prior compatible config generation with current secrets")
    rollback.add_argument("--config-generation", required=True)
    rollback.add_argument("--expected-secret-generation", required=True)
    rollback.add_argument("--acknowledge", action="append", default=[])

    secret = commands.add_parser("secret", help="Perform a write-only logical-secret mutation")
    secret.add_argument(
        "secret_operation",
        choices=("create_secret", "replace_secret", "rotate_secret", "remove_secret"),
    )
    secret.add_argument("logical_id")
    secret.add_argument("--expected-secret-generation", required=True)
    secret.add_argument(
        "--value-stdin",
        action="store_true",
        help="Read one secret line from stdin instead of a hidden prompt; values are never accepted as argv",
    )

    commands.add_parser("recover", help="Recover pending managed-authoring and secret transactions")
    cutover = commands.add_parser("cutover", help="Irreversibly require canonical runtime startup")
    cutover.add_argument("--acknowledge-one-way", action="store_true", required=True)
    compatibility = commands.add_parser(
        "compatibility",
        help="Manage typed satellite runtime compatibility evidence",
    )
    compatibility_commands = compatibility.add_subparsers(
        dest="compatibility_command",
        required=True,
    )
    compatibility_accept = compatibility_commands.add_parser(
        "accept",
        help="Validate and accept one satellite runtime compatibility report",
    )
    compatibility_accept.add_argument("--satellite-id", required=True)
    compatibility_accept.add_argument("--report", required=True)
    return root


def _offline_result(args: argparse.Namespace) -> dict[str, object]:
    authoring_root = None if args.authoring_root is None else Path(args.authoring_root)
    store = GenerationStore(Path(args.offline_store))
    store.validate_initialized()
    service = ConfigurationService(
        store,
        authoring_mode=args.authoring_mode,
        authoring_root=authoring_root,
    )
    with ServicePresenceLock(store.root):
        if args.command == "status":
            result: object = asdict(service.status())
        elif args.command == "review":
            review = service.review_candidate(Path(args.candidate), actor="host_local_cli")
            result = HostLocalDispatcher(service)._review_result(review)
        elif args.command == "activate":
            candidate = Path(args.candidate)
            transaction = service.activate_candidate(
                candidate,
                expected_authored_revision=snapshot_candidate(candidate).authored_revision,
                expected_secret_generation_id=args.expected_secret_generation,
                actor="host_local_cli",
                acknowledgements=frozenset(_acknowledgements(args)),
            )
            result = HostLocalDispatcher._transaction_result(transaction)
        elif args.command == "apply":
            transaction = service.replace_authored_candidate(
                Path(args.candidate),
                expected_authored_revision=args.expected_authored_revision,
                expected_secret_generation_id=args.expected_secret_generation,
                actor="host_local_cli",
                acknowledgements=frozenset(_acknowledgements(args)),
            )
            result = HostLocalDispatcher._authoring_result(transaction)
        elif args.command == "rollback":
            transaction = service.rollback(
                args.config_generation,
                expected_secret_generation_id=args.expected_secret_generation,
                actor="host_local_cli",
                acknowledgements=frozenset(_acknowledgements(args)),
            )
            result = HostLocalDispatcher._transaction_result(transaction)
        elif args.command == "secret":
            if authoring_root is None:
                raise HostLocalProtocolError("Offline secret mutation requires --authoring-root.")
            mutation = service.mutate_secret(
                authoring_root,
                operation=args.secret_operation,
                logical_id=args.logical_id,
                value=_secret_value(args),
                expected_secret_generation_id=args.expected_secret_generation,
                actor="host_local_cli",
            )
            result = HostLocalDispatcher._secret_result(mutation)
        elif args.command == "recover":
            if authoring_root is None:
                raise HostLocalProtocolError("Offline recovery requires --authoring-root.")
            authoring = (
                service.recover_authoring_transactions(actor="host_local_cli")
                if args.authoring_mode == "managed_writable"
                else ()
            )
            secrets = service.recover_secret_transactions(authoring_root, actor="host_local_cli")
            result = {
                "authoring_transaction_ids": list(authoring),
                "secret_transaction_ids": list(secrets),
            }
        elif args.command == "cutover":
            cutover = service.require_canonical_runtime(
                actor="host_local_cli",
                acknowledge_one_way=args.acknowledge_one_way,
            )
            result = HostLocalDispatcher(service)._runtime_cutover_result(cutover)
        elif args.command == "compatibility" and args.compatibility_command == "accept":
            report = SatelliteRuntimeCompatibility.model_validate(
                _compatibility_report(args.report)
            )
            accepted = service.accept_satellite_runtime_compatibility(
                args.satellite_id,
                report,
                actor="host_local_cli",
            )
            result = HostLocalDispatcher._runtime_compatibility_result(accepted)
        else:
            raise HostLocalProtocolError("Unsupported offline CLI command.")
    return {"format": HOST_LOCAL_PROTOCOL_FORMAT, "ok": True, "result": result}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        response = (
            HostLocalConfigurationClient(Path(args.socket)).request(build_request(args))
            if args.socket is not None
            else _offline_result(args)
        )
    except Exception as exc:
        error = (
            {"code": "transport_error", "message": str(exc)}
            if args.socket is not None and isinstance(exc, (OSError, HostLocalProtocolError))
            else HostLocalDispatcher._error(exc)
        )
        response = {"format": HOST_LOCAL_PROTOCOL_FORMAT, "ok": False, "error": error}
        if error["code"] == "internal_error":
            response["error"] = {"code": "offline_error", "message": "Offline configuration operation failed."}
        print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    stream = sys.stdout if response.get("ok") is True else sys.stderr
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True), file=stream)
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
