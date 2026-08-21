from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "distribution" / "core"
if not PUBLIC_ROOT.is_dir():
    PUBLIC_ROOT = ROOT
RUNBOOK = ROOT / "docs" / "runbooks" / "standard-installation.md"
CLI_REFERENCE = ROOT / "docs" / "reference" / "administration-cli.md"
PUBLIC_README = PUBLIC_ROOT / "README.md"
PUBLIC_DOCS_INDEX = PUBLIC_ROOT / "docs" / "README.md"
DEPLOYMENT_INVENTORY = PUBLIC_ROOT / "docs" / "runbooks" / "deployment-inventory.md"


COMMANDS = {
    "status",
    "diagnostics-export",
    "preflight",
    "stage-plan",
    "stage",
    "assemble-plan",
    "assemble",
    "update-assemble-plan",
    "update-assemble",
    "service-plan",
    "service-install",
    "activate-plan",
    "activate",
    "activate-recover",
    "update-plan",
    "update",
    "update-recover",
    "rollback-plan",
    "rollback",
}


def test_standard_installation_runbook_covers_the_complete_cli_lifecycle() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    reference = CLI_REFERENCE.read_text(encoding="utf-8")

    for command in COMMANDS:
        assert command in runbook
        assert f"`{command}`" in reference

    for required in (
        "Debian 13 on amd64",
        "minimal-brain",
        "sha256sum --check",
        "mktemp -d",
        "/srv/oracle",
        "--approved-plan",
        "required_safety_acknowledgements",
        "--acknowledge",
        "--runtime-compatibility-store",
        "systemctl is-enabled",
        "/health/config",
        "recovered_failed",
        "previous-known-good",
        "clean Debian baseline",
    ):
        assert required in runbook

    assert 'sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json update-assemble-plan' in runbook
    assert "validates the existing selected secret generation" in reference


def test_public_navigation_exposes_installation_and_administration_contracts() -> None:
    readme = PUBLIC_README.read_text(encoding="utf-8")
    index = PUBLIC_DOCS_INDEX.read_text(encoding="utf-8")

    assert "docs/runbooks/standard-installation.md" in readme
    assert "docs/reference/administration-cli.md" in readme
    assert "runbooks/standard-installation.md" in index
    assert "reference/administration-cli.md" in index


def test_distribution_no_longer_calls_the_validated_installer_non_operational() -> None:
    inventory = DEPLOYMENT_INVENTORY.read_text(encoding="utf-8")

    assert "not an operational runbook" not in inventory
    assert "standard installation runbook" in inventory


def test_reusable_operator_docs_contain_no_private_household_locator() -> None:
    combined = RUNBOOK.read_text(encoding="utf-8") + CLI_REFERENCE.read_text(encoding="utf-8")
    assert re.search(r"/home/[A-Za-z0-9._-]+", combined) is None
    assert re.search(r"\b(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))\.[0-9.]+", combined) is None
