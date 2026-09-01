from __future__ import annotations

from types import SimpleNamespace

from oracle_app.handlers.system import SystemHandler
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_session_state, get_utility_context


def _handler() -> SystemHandler:
    return SystemHandler(
        household_settings=SimpleNamespace(household=SimpleNamespace(timezone="America/New_York")),  # type: ignore[arg-type]
    )


def _dispatch(text: str, *, source: str = "test-source", session_id: str = "conversion-session") -> DispatchPlan:
    return DispatchPlan(
        target="system",
        hook="system.calculation",
        payload={"action": "calculation", "text": text, "source": source, "session_id": session_id},
        status="planned",
    )


def test_handler_records_canonical_base_value_and_uses_conversion_context() -> None:
    source, session_id = "test-source", "typed-conversion"
    clear_session_state(source, session_id)
    handler = _handler()

    first = handler.handle(_dispatch("what is 5 miles in kilometers", source=source, session_id=session_id), object())
    assert first.status == "executed"
    assert first.result["speech"] == "5 miles is about 8.05 kilometers."
    context = get_utility_context(source, session_id, kind="conversion")
    assert context is not None
    assert context["payload"] == {
        "value": "8046.72",
        "dimension": "length",
        "source_unit": "miles",
        "target_unit": "kilometers",
        "display_text": "8.05 kilometers",
    }

    second = handler.handle(_dispatch("what about 12 miles", source=source, session_id=session_id), object())
    assert second.status == "executed"
    assert second.result["speech"] == "12 miles is about 19.31 kilometers."


def test_conversion_does_not_overwrite_prior_math_context() -> None:
    source, session_id = "test-source", "math-conversion-slots"
    clear_session_state(source, session_id)
    handler = _handler()
    handler.handle(_dispatch("what is 18% of 240", source=source, session_id=session_id), object())
    math_before = get_utility_context(source, session_id, kind="calculation")
    handler.handle(_dispatch("convert 5 miles to kilometers", source=source, session_id=session_id), object())
    assert get_utility_context(source, session_id, kind="calculation") == math_before
    assert get_utility_context(source, session_id, kind="conversion") is not None


def test_handler_surfaces_dimensional_failure_without_replacing_context() -> None:
    source, session_id = "test-source", "conversion-failure"
    clear_session_state(source, session_id)
    handler = _handler()
    handler.handle(_dispatch("convert 5 miles to kilometers", source=source, session_id=session_id), object())
    before = get_utility_context(source, session_id, kind="conversion")

    failed = handler.handle(_dispatch("convert 2 cups to grams", source=source, session_id=session_id), object())
    assert failed.status == "failed"
    assert failed.result["error"] == "calculation_unavailable"
    assert "density" in failed.result["detail"]
    assert get_utility_context(source, session_id, kind="conversion") == before
