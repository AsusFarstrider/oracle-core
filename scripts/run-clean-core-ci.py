#!/usr/bin/env python3
"""Run every promoted core test and reject any undeclared skip."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "clean-core-allowed-skips.txt"


class SkipCollector:
    def __init__(self) -> None:
        self.node_ids: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped:
            self.node_ids.add(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.node_ids.add(report.nodeid)


def _read_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-list", type=Path, help="Developer-only explicit test path list.")
    parser.add_argument("--record-skips", type=Path, help="Write observed skip IDs instead of enforcing the baseline.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.test_list:
        tests = _read_lines(args.test_list)
    else:
        tests = [str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests").glob("test_*.py"))]
    if not tests:
        print("clean-core CI found no required tests", file=sys.stderr)
        return 2

    collector = SkipCollector()
    extra = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    result = pytest.main([*tests, "--strict-markers", *extra], plugins=[collector])
    observed = sorted(collector.node_ids)
    if args.record_skips:
        args.record_skips.write_text("\n".join(observed) + ("\n" if observed else ""), encoding="utf-8")
    else:
        expected = set(_read_lines(BASELINE))
        actual = set(observed)
        if actual != expected:
            for node_id in sorted(actual - expected):
                print(f"unexpected skip: {node_id}", file=sys.stderr)
            for node_id in sorted(expected - actual):
                print(f"declared skip did not occur: {node_id}", file=sys.stderr)
            return 3
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
