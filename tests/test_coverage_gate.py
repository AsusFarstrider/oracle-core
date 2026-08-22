from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from scripts.coverage_gate import evaluate


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for path in ("server/brain.py", "server/unused.py", "satellite/runtime.py"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def _summary(*, covered_lines: int = 1, statements: int = 1, covered_branches: int = 1, branches: int = 1) -> dict[str, object]:
    return {
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }


def _policy(*, ratchet: dict[str, int] | None = None, exclusions: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "format_version": 1,
        "branch_coverage_required": True,
        "surfaces": {
            "brain": {
                "roots": ["server"],
                "exclusions": exclusions or [],
                "ratchet": ratchet,
            },
            "satellite": {
                "roots": ["satellite"],
                "exclusions": [],
                "ratchet": ratchet,
            },
        },
    }


def test_every_tracked_module_is_measured_and_aggregated(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = {
        "files": {
            "server/brain.py": _summary(covered_lines=3, statements=4, covered_branches=2, branches=4),
            "server/unused.py": _summary(covered_lines=0, statements=2, covered_branches=0, branches=0),
            "satellite/runtime.py": _summary(),
        }
    }

    result, errors = evaluate(repo_root=repo, report=report, policy=_policy())

    assert errors == []
    assert result["ok"] is True
    brain = result["surfaces"]["brain"]
    assert brain["tracked_modules"] == 2
    assert brain["measured_modules"] == 2
    assert brain["line_basis_points"] == 5000
    assert brain["branch_basis_points"] == 5000
    assert brain["zero_hit_modules"] == ["server/unused.py"]


def test_missing_tracked_module_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = {
        "files": {
            "server/brain.py": _summary(),
            "satellite/runtime.py": _summary(),
        }
    }

    _result, errors = evaluate(repo_root=repo, report=report, policy=_policy())

    assert errors == [
        "brain: tracked modules missing from coverage report: server/unused.py"
    ]


def test_narrow_reasoned_exclusion_is_accepted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = {
        "files": {
            "server/brain.py": _summary(),
            "satellite/runtime.py": _summary(),
        }
    }
    policy = _policy(
        exclusions=[{"path": "server/unused.py", "reason": "generated binding"}]
    )

    result, errors = evaluate(repo_root=repo, report=report, policy=policy)

    assert errors == []
    assert result["surfaces"]["brain"]["excluded_modules"] == ["server/unused.py"]


def test_ratchets_are_enforced_in_basis_points(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = {
        "files": {
            "server/brain.py": _summary(),
            "server/unused.py": _summary(covered_lines=0, statements=1, covered_branches=0, branches=1),
            "satellite/runtime.py": _summary(),
        }
    }
    policy = _policy(ratchet={"line_basis_points": 5001, "branch_basis_points": 5001})

    _result, errors = evaluate(repo_root=repo, report=report, policy=policy)

    assert errors == [
        "brain: line coverage 5000bp is below 5001bp",
        "brain: branch coverage 5000bp is below 5001bp",
    ]


def test_absolute_report_paths_are_normalized(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = {
        "files": {
            str(repo / "server/brain.py"): _summary(),
            str(repo / "server/unused.py"): _summary(),
            str(repo / "satellite/runtime.py"): _summary(),
        }
    }

    result, errors = evaluate(repo_root=repo, report=report, policy=_policy())

    assert errors == []
    assert result["ok"] is True


def test_policy_fixture_is_valid_json() -> None:
    policy_path = Path(__file__).with_name("coverage-policy.json")
    assert json.loads(policy_path.read_text(encoding="utf-8"))["format_version"] == 1


def _load_clean_core_runner():
    path = Path(__file__).parents[1] / "scripts" / "run-clean-core-ci.py"
    spec = importlib.util.spec_from_file_location("clean_core_ci_for_coverage_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_core_runner_uses_one_measured_pass_and_enforces_gate(tmp_path: Path) -> None:
    runner = _load_clean_core_runner()
    report = tmp_path / "coverage.json"
    gate_result = SimpleNamespace(returncode=0)
    with (
        patch.object(sys, "argv", ["run-clean-core-ci.py", "--coverage-json", str(report), "--", "-q"]),
        patch.object(runner.pytest, "main", return_value=0) as pytest_main,
        patch.object(runner, "_read_lines", return_value=[]),
        patch.object(runner.subprocess, "run", return_value=gate_result) as run,
    ):
        assert runner.main() == 0

    pytest_args = pytest_main.call_args.args[0]
    assert "--cov" in pytest_args
    assert "--cov-branch" in pytest_args
    assert f"--cov-report=json:{report.resolve()}" in pytest_args
    run.assert_called_once_with(
        [sys.executable, str(runner.ROOT / "scripts" / "coverage_gate.py"), str(report.resolve())],
        cwd=runner.ROOT,
        check=False,
    )


def test_clean_core_runner_does_not_gate_a_failed_suite(tmp_path: Path) -> None:
    runner = _load_clean_core_runner()
    with (
        patch.object(sys, "argv", ["run-clean-core-ci.py", "--coverage-json", str(tmp_path / "coverage.json")]),
        patch.object(runner.pytest, "main", return_value=2),
        patch.object(runner, "_read_lines", return_value=[]),
        patch.object(runner.subprocess, "run") as run,
    ):
        assert runner.main() == 2
    run.assert_not_called()
