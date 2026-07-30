from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oracle_app.logging_setup import configure_brain_logging

configure_brain_logging()

from oracle_app.api import app
