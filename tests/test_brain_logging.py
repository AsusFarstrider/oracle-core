from __future__ import annotations

import io
import logging

from oracle_app.logging_setup import configure_brain_logging


def test_configure_brain_logging_emits_info_records() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("oracle-brain")
    logger.handlers.clear()
    logger.propagate = True
    logger = configure_brain_logging(stream=stream)

    child = logging.getLogger("oracle-brain.test")
    child.info("facts_requested source=test session_id=s1")

    output = stream.getvalue()
    assert "INFO" in output
    assert "oracle-brain.test" in output
    assert "facts_requested source=test session_id=s1" in output
    assert logger.propagate is False


def test_configure_brain_logging_is_idempotent() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("oracle-brain")
    logger.handlers.clear()
    logger.propagate = True

    configure_brain_logging(stream=stream)
    configure_brain_logging(stream=stream)

    marked_handlers = [handler for handler in logger.handlers if getattr(handler, "_oracle_brain_stdout_handler", False)]
    assert len(marked_handlers) == 1
