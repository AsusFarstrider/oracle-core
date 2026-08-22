from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest import mock

from oracle_app.full_production_host import build_host_plan, secret_asset_identity


ROOT = Path(__file__).resolve().parents[1]


def _deployment(command_path: Path | None = None) -> dict[str, object]:
    deployment = {
        "installation_profiles": ["full-production-brain"],
        "full_production_brain": {
            "host_capabilities": {
                "commands": {
                    "docker": "/usr/bin/docker",
                    "ffmpeg": "/usr/bin/ffmpeg",
                    "findmnt": "/usr/bin/findmnt",
                    "mount": "/usr/bin/mount",
                    "reboot": "/usr/sbin/reboot",
                    "snmpget": "/usr/bin/snmpget",
                    "ssh": "/usr/bin/ssh",
                    "sshpass": "/usr/bin/sshpass",
                    "sudo": "/usr/bin/sudo",
                    "sync": "/usr/bin/sync",
                    "systemctl": "/usr/bin/systemctl",
                    "umount": "/usr/bin/umount",
                },
                "supplementary_groups": ["service-access", "storage-access"],
                "writable_paths": ["/srv/example-storage"],
            },
            "external_provider_requirements": {
                "ollama": {
                    "binary": "/usr/local/bin/ollama",
                    "model": "example-model:latest",
                    "model_digest": "78fad5d182a7c33065e153a5f8ba210754207ba9d91973f57dffa7f487363753",
                    "service": "ollama.service",
                    "version": "0.20.2",
                }
            },
            "secret_assets": [{
                "destination": "secrets/provider-assets/provider_ssh_identity",
                "id": "provider_ssh_identity",
                "mode": "0600",
            }],
        },
    }
    if command_path is not None:
        capabilities = deployment["full_production_brain"]["host_capabilities"]
        capabilities["commands"] = {
            name: str(command_path)
            for name in capabilities["commands"]
        }
    return deployment


def test_full_production_host_plan_binds_exact_commands_groups_provider_and_secret() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        secret = root / "identity"
        secret.write_text("private-key-material", encoding="utf-8")
        secret.chmod(0o600)
        command = root / "command"
        command.write_text("#!/bin/sh\n", encoding="utf-8")
        command.chmod(0o700)
        sudoers = root / "profile.sudoers"
        sudoers.write_text(
            "Cmnd_Alias ORACLE_PROFILE = /usr/bin/systemctl restart example.service\n"
            "oracle ALL=(root) NOPASSWD: ORACLE_PROFILE\n",
            encoding="utf-8",
        )
        group_records = {
            "service-access": SimpleNamespace(gr_name="service-access", gr_gid=103, gr_mem=[]),
            "storage-access": SimpleNamespace(gr_name="storage-access", gr_gid=1002, gr_mem=[]),
        }
        with (
            mock.patch("oracle_app.full_production_host.pwd.getpwnam", return_value=SimpleNamespace(pw_name="oracle", pw_gid=999)),
            mock.patch("oracle_app.full_production_host.grp.getgrnam", side_effect=lambda name: group_records[name]),
            mock.patch("oracle_app.full_production_host.grp.getgrall", return_value=list(group_records.values())),
            mock.patch("oracle_app.full_production_host.Path.is_dir", return_value=True),
        ):
            plan = build_host_plan(
                _deployment(command),
                secret,
                sudoers,
                root=root,
                provider_probe=lambda _binary, _model: {
                    "version_output": "ollama version is 0.20.2",
                    "model_digest": "78fad5d182a7c33065e153a5f8ba210754207ba9d91973f57dffa7f487363753",
                },
            )

        assert plan["profile"] == "full-production-brain"
        assert plan["groups"]["missing_for_oracle"] == ["service-access", "storage-access"]
        assert plan["secret_asset"]["mode"] == "0600"
        assert plan["secret_asset"]["disposition"] == "absent"
        assert plan["external_provider"]["ollama"]["model"] == "example-model:latest"
        assert str(plan["identity"]).startswith("oracle-full-production-host-plan-v1:sha256:")


def test_secret_provider_asset_rejects_group_readability() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        secret = Path(temporary) / "identity"
        secret.write_text("private-key-material", encoding="utf-8")
        secret.chmod(0o640)

        try:
            secret_asset_identity(secret)
        except RuntimeError as exc:
            assert "readable by another" in str(exc)
        else:
            raise AssertionError("group-readable secret provider asset was accepted")


def test_elevated_full_production_helpers_suppress_staged_tree_bytecode() -> None:
    for relative in ("scripts/full-production-host.py", "scripts/full-production-data.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assignment = source.index("sys.dont_write_bytecode = True")
        first_local_import = source.index("from core_artifact import")
        assert assignment < first_local_import
