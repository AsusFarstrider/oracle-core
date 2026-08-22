"""Standard Debian entrypoint through one complete installed activation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oracle_app.installation_runtime import record_running_activation
from oracle_app.logging_setup import configure_brain_logging

record_running_activation()
configure_brain_logging()

from oracle_app.api import app

__all__ = ["app"]
