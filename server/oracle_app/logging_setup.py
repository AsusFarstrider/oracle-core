from __future__ import annotations

import logging
import sys
from typing import TextIO


_BRAIN_LOGGER_NAME = "oracle-brain"
_HANDLER_MARKER = "_oracle_brain_stdout_handler"


def configure_brain_logging(*, stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger(_BRAIN_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return logger

    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
