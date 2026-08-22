#!/usr/bin/env python3
"""Bounded systemd lifecycle helper for a standard Oracle installation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


APPLICATION_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = APPLICATION_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from oracle_app.installation_runtime import recover_after_process_exit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("recover-after-exit",))
    args = parser.parse_args(argv)
    if args.operation == "recover-after-exit":
        result = recover_after_process_exit()
        print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
