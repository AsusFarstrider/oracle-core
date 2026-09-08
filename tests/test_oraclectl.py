from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("oraclectl", str(ROOT / "scripts/oraclectl"))
SPEC = importlib.util.spec_from_loader("oraclectl", LOADER)
assert SPEC is not None
oraclectl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oraclectl
LOADER.exec_module(oraclectl)


def _activation(root: Path, name: str, digest: str = "a" * 64) -> str:
    activation_id = "oracle-installation-activation-v1:sha256:" + digest
    directory = root / "activations" / ("activation-" + digest)
    directory.mkdir(parents=True)
    (directory / "activation.json").write_text(json.dumps({
        "activation_id": activation_id,
        "core": {"commit": "b" * 40, "git_tree": "c" * 40},
        "household_deployment_revision": "oracle-household-deployment-v1:sha256:" + "d" * 64,
        "configuration_activation_identity": "activation_test",
        "python_environment_identity": "environment_test",
    }), encoding="utf-8")
    selection = root / "selection"
    selection.mkdir(exist_ok=True)
    (selection / name).symlink_to(directory)
    return activation_id


def test_activation_inspection_survives_missing_active_selector(tmp_path: Path) -> None:
    result = oraclectl.activation_state(tmp_path)
    assert result["active"] == {"name": "active", "present": False, "valid": False, "detail": "selector is absent"}


def test_activation_inspection_rejects_selector_outside_managed_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "activations").mkdir()
    selection = tmp_path / "selection"
    selection.mkdir()
    (selection / "active").symlink_to(outside)
    result = oraclectl.inspect_selector(tmp_path, "active")
    assert result["valid"] is False
    assert "outside" in str(result["detail"])


def test_status_is_bounded_to_cheap_brain_and_config_health(tmp_path: Path) -> None:
    _activation(tmp_path, "active")
    responses = {
        "/health": {"status": "ok", "service": "oracle-brain"},
        "/api/admin/health/config": {"ok": True, "configuration": {"applied_generation": {"satellite_projection_activation_ids": {"sat-1": "p1"}}}},
    }
    with mock.patch.object(oraclectl, "_service_state", return_value={"active": "active", "enabled": "enabled", "substate": "running", "main_pid": 1, "restarts": 0, "uptime_seconds": 10}), mock.patch.object(
        oraclectl, "_http_json", side_effect=lambda path, **_kwargs: responses[path]
    ) as http:
        result = oraclectl.build_status(tmp_path)
    assert result["basically_up"] is True
    assert result["configured_satellite_count"] == 1
    assert [call.args[0] for call in http.call_args_list] == ["/health", "/api/admin/health/config"]


def test_doctor_uses_structured_diagnostics(tmp_path: Path) -> None:
    _activation(tmp_path, "active")
    with mock.patch.object(oraclectl, "build_status", return_value={
        "active": {"valid": True},
        "service": {"active": "active", "substate": "running"},
    }), mock.patch.object(oraclectl, "_diagnose_endpoint", return_value=oraclectl.Diagnostic("PASS", "test", "ok")):
        result = oraclectl.build_doctor(tmp_path)
    assert result["overall"] == "PASS"
    assert all(set(item) == {"severity", "component", "explanation", "remediation"} for item in result["diagnostics"])


def test_ambiguous_probe_is_degraded_not_doctor_failure() -> None:
    with mock.patch.object(oraclectl, "_http_json", side_effect=TimeoutError):
        result = oraclectl._diagnose_endpoint("calendar", "/health")
    assert result.severity == "UNKNOWN"


def test_network_diagnosis_reads_canonical_nested_status() -> None:
    with mock.patch.object(oraclectl, "_http_json", return_value={
        "ok": True, "network": {"status": "healthy", "severity": "none"}
    }):
        result = oraclectl._diagnose_endpoint("network", "/api/admin/network/status")
    assert result.severity == "PASS"


def test_restart_preserves_activation_and_waits_for_health(tmp_path: Path) -> None:
    activation_id = _activation(tmp_path, "active")
    with mock.patch.object(oraclectl.subprocess, "run", return_value=mock.Mock(returncode=0)), mock.patch.object(
        oraclectl, "_http_json", return_value={"status": "ok"}
    ):
        result = oraclectl.restart_brain(tmp_path, timeout_seconds=0.1)
    assert result["activation_before"] == activation_id
    assert result["activation_after"] == activation_id
    assert result["activation_unchanged"] is True


def test_restart_refuses_invalid_active_selector(tmp_path: Path) -> None:
    with mock.patch.object(oraclectl.subprocess, "run") as run:
        try:
            oraclectl.restart_brain(tmp_path)
        except RuntimeError as exc:
            assert "refusing restart" in str(exc)
        else:
            raise AssertionError("restart should fail")
    run.assert_not_called()
