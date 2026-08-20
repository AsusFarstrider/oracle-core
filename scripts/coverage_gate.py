#!/usr/bin/env python3
"""Reconcile measured production modules and enforce per-surface coverage ratchets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "tests" / "coverage-policy.json"


@dataclass(frozen=True)
class Totals:
    covered_lines: int = 0
    statements: int = 0
    covered_branches: int = 0
    branches: int = 0

    def add(self, summary: dict[str, Any]) -> "Totals":
        return Totals(
            covered_lines=self.covered_lines + int(summary["covered_lines"]),
            statements=self.statements + int(summary["num_statements"]),
            covered_branches=self.covered_branches + int(summary["covered_branches"]),
            branches=self.branches + int(summary["num_branches"]),
        )

    @property
    def line_basis_points(self) -> int:
        return _basis_points(self.covered_lines, self.statements)

    @property
    def branch_basis_points(self) -> int:
        return _basis_points(self.covered_branches, self.branches)


def _basis_points(covered: int, total: int) -> int:
    if total == 0:
        return 10_000
    return (covered * 10_000) // total


def _tracked_modules(repo_root: Path, roots: list[str]) -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--", *roots], cwd=repo_root, text=True
    )
    return {
        path
        for path in output.splitlines()
        if path.endswith(".py") and any(path == root or path.startswith(f"{root}/") for root in roots)
    }


def _normalize_files(report: dict[str, Any], repo_root: Path) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw_path, value in report.get("files", {}).items():
        path = Path(raw_path)
        if path.is_absolute():
            try:
                path = path.relative_to(repo_root)
            except ValueError:
                continue
        normalized[path.as_posix()] = value
    return normalized


def evaluate(
    *, repo_root: Path, report: dict[str, Any], policy: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if policy.get("format_version") != 1:
        errors.append("coverage policy format_version must be 1")
    if not policy.get("branch_coverage_required"):
        errors.append("coverage policy must require branch coverage")

    measured = _normalize_files(report, repo_root)
    output: dict[str, Any] = {"format_version": 1, "surfaces": {}}
    claimed: set[str] = set()

    for name, surface in policy.get("surfaces", {}).items():
        roots = [str(root).rstrip("/") for root in surface.get("roots", [])]
        tracked = _tracked_modules(repo_root, roots)
        exclusions = surface.get("exclusions", [])
        excluded: set[str] = set()
        for entry in exclusions:
            path = str(entry.get("path", ""))
            reason = str(entry.get("reason", "")).strip()
            if not path or not reason:
                errors.append(f"{name}: every exclusion requires an exact path and reason")
                continue
            if path not in tracked:
                errors.append(f"{name}: excluded path is not a tracked production module: {path}")
            excluded.add(path)
        claimed.update(excluded)

        expected = tracked - excluded
        missing = sorted(expected - measured.keys())
        if missing:
            errors.append(f"{name}: tracked modules missing from coverage report: {', '.join(missing)}")

        totals = Totals()
        zero_hit: list[str] = []
        for path in sorted(expected & measured.keys()):
            claimed.add(path)
            summary = measured[path].get("summary", {})
            required = {
                "covered_lines",
                "num_statements",
                "covered_branches",
                "num_branches",
            }
            absent = required - summary.keys()
            if absent:
                errors.append(f"{name}: incomplete summary for {path}: {', '.join(sorted(absent))}")
                continue
            totals = totals.add(summary)
            if int(summary["num_statements"]) and not int(summary["covered_lines"]):
                zero_hit.append(path)

        ratchet = surface.get("ratchet")
        if ratchet is not None:
            line_floor = int(ratchet["line_basis_points"])
            branch_floor = int(ratchet["branch_basis_points"])
            if totals.line_basis_points < line_floor:
                errors.append(
                    f"{name}: line coverage {totals.line_basis_points}bp is below {line_floor}bp"
                )
            if totals.branch_basis_points < branch_floor:
                errors.append(
                    f"{name}: branch coverage {totals.branch_basis_points}bp is below {branch_floor}bp"
                )

        output["surfaces"][name] = {
            "tracked_modules": len(tracked),
            "measured_modules": len(expected & measured.keys()),
            "excluded_modules": sorted(excluded),
            "zero_hit_modules": zero_hit,
            "covered_lines": totals.covered_lines,
            "statements": totals.statements,
            "line_basis_points": totals.line_basis_points,
            "covered_branches": totals.covered_branches,
            "branches": totals.branches,
            "branch_basis_points": totals.branch_basis_points,
            "ratchet": ratchet,
        }

    production_measured = {
        path
        for path in measured
        if any(
            path == root or path.startswith(f"{root}/")
            for surface in policy.get("surfaces", {}).values()
            for root in surface.get("roots", [])
        )
    }
    unclaimed = sorted(production_measured - claimed)
    if unclaimed:
        errors.append(f"measured production modules have no surface owner: {', '.join(unclaimed)}")
    output["ok"] = not errors
    output["errors"] = errors
    return output, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()

    report = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    result, errors = evaluate(repo_root=args.repo_root.resolve(), report=report, policy=policy)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
