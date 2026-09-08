from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oracle_app.facts_context import resolve_facts_context, retain_facts_context
from oracle_app.facts import build_facts_request, lookup_facts, maybe_summarize_facts_result
from oracle_app.facts_summarizer import (
    FACTS_SUMMARIZER_RESULT_SCHEMA,
    FactsSummarizationResult,
    summarize_facts_result,
    validate_facts_summarizer_result,
)
from oracle_app.inference import (
    InferenceClient,
    InferenceContractError,
    InferenceExecutionSettings,
    InferenceProviderSettings,
)
from oracle_app.inference_bridges import InferenceProviderResponse
from oracle_app.schemas import (
    DispatchPlan,
    FactsAnswer,
    FactsEvidence,
    FactsProviderInfo,
    FactsProviderResult,
    FactsRetrievalInfo,
)
from oracle_app.handlers.facts import FactsHandler
from oracle_app.provider_bridges.facts_static import StaticFactsBridge
from oracle_app.facts_wikipedia_policy import WikipediaQuestionPolicy
from oracle_app.replies import build_reply_text
from oracle_app.command_events import clear_command_interim_events, list_command_interim_events
from oracle_app.admin_facts_routes import _run_admin_summarizer
from oracle_app.capabilities.information import FactsCapability
from oracle_app.session_state import clear_all_sessions, get_informational_context, get_pending_state


def _result(
    *,
    query: str = "When was Alan Turing born?",
    answer: str = "Alan Turing was born on 23 June 1912 in London.",
    title: str = "Alan Turing",
    snippet: str | None = None,
) -> FactsProviderResult:
    return FactsProviderResult(
        status="answered",
        query=query,
        answer=FactsAnswer(text=answer),
        evidence=[
            FactsEvidence(
                title=title,
                snippet=snippet or answer,
                source_name="Wikipedia",
                source_type="wikipedia",
                provenance={"page_title": title, "url": f"https://example.invalid/{title}"},
            )
        ],
        provider=FactsProviderInfo(id="wikipedia_api", name="Wikipedia API"),
        retrieval=FactsRetrievalInfo(method="fixture", notes=[]),
    )


@pytest.fixture(autouse=True)
def _sessions():
    clear_all_sessions()
    clear_command_interim_events()
    yield
    clear_all_sessions()
    clear_command_interim_events()


def _client(*responses: str) -> tuple[InferenceClient, Mock, Mock]:
    client = InferenceClient(
        InferenceExecutionSettings(
            enabled=True,
            base_url=None,
            model=None,
            timeout_seconds=None,
            keep_alive=None,
            options={},
            fallback_model=None,
            fallback_timeout_seconds=None,
            providers={
                "luna": InferenceProviderSettings(
                    provider_type="openai_luna",
                    enabled=True,
                    base_url="https://api.openai.com",
                    model="gpt-5.6-luna",
                    timeout_seconds=4,
                    api_key="fixture",
                ),
                "ollama": InferenceProviderSettings(
                    provider_type="ollama",
                    enabled=True,
                    base_url="http://127.0.0.1:11434",
                    model="phi4-mini:latest",
                    timeout_seconds=4,
                ),
            },
            consumer_orders={"facts_summarizer": ("luna", "ollama")},
            consumer_total_timeout_seconds={"facts_summarizer": 8},
        )
    )
    luna = Mock()
    ollama = Mock()
    luna.generate.return_value = InferenceProviderResponse(responses[0], "gpt-5.6-luna")
    if len(responses) > 1:
        ollama.generate.return_value = InferenceProviderResponse(responses[1], "phi4-mini:latest")
    client._providers = {"luna": luna, "ollama": ollama}  # noqa: SLF001
    return client, luna, ollama


def test_facts_summarizer_uses_consumer_contract_and_returns_safe_identity() -> None:
    client, luna, ollama = _client(
        json.dumps({"status": "summary", "summary": "Alan Turing was born on 23 June 1912."})
    )

    outcome = summarize_facts_result(_result(), inference=client)

    assert outcome.status == "summary"
    assert outcome.summary == "Alan Turing was born on 23 June 1912."
    assert outcome.provider_id == "luna"
    assert luna.generate.call_args.kwargs["json_schema"] == FACTS_SUMMARIZER_RESULT_SCHEMA
    ollama.generate.assert_not_called()


def test_valid_insufficient_is_terminal_without_second_provider() -> None:
    client, luna, ollama = _client(json.dumps({"status": "insufficient", "summary": ""}))

    outcome = summarize_facts_result(_result(), inference=client)

    assert outcome.status == "insufficient"
    assert outcome.summary is None
    assert [attempt.outcome for attempt in outcome.attempts] == ["success"]
    assert luna.generate.call_count == 1
    ollama.generate.assert_not_called()


def test_facts_summaries_are_not_cached() -> None:
    client, luna, _ollama = _client(
        json.dumps({"status": "summary", "summary": "Alan Turing was born on 23 June 1912."})
    )

    summarize_facts_result(_result(), inference=client)
    summarize_facts_result(_result(), inference=client)

    assert luna.generate.call_count == 2


def test_facts_retrieval_cache_avoids_second_provider_call(monkeypatch) -> None:
    settings = {
        "enabled": True,
        "provider": "static",
        "cache_enabled": True,
        "cache_ttl_seconds": 60,
        "static_items": [
            {
                "queries": ["What is Oracle?"],
                "answer": {"text": "A household assistant."},
            }
        ],
    }
    provider_calls: list[str] = []
    original_lookup = StaticFactsBridge.lookup

    def counted_lookup(self, request, *, settings):
        provider_calls.append(request.query)
        return original_lookup(self, request, settings=settings)

    monkeypatch.setattr(StaticFactsBridge, "lookup", counted_lookup)
    request = build_facts_request(query="What is Oracle?", source="office", session_id="session-1")

    first = lookup_facts(request, settings=settings)
    second = lookup_facts(request, settings=settings)

    assert first.status == second.status == "answered"
    assert provider_calls == ["What is Oracle?"]
    assert "cache_hit" not in first.retrieval.notes
    assert "cache_hit" in second.retrieval.notes


def test_wikipedia_date_policy_selects_date_bearing_page_sentence() -> None:
    policy = WikipediaQuestionPolicy()
    plan = policy.build_search_plan("When was Alan Turing born?")
    summary = {"title": "Alan Turing", "extract": "Alan Turing was an English mathematician."}

    enriched = policy.enrich_date(
        summary,
        "Alan Mathison Turing (; 23 June 1912 – 7 June 1954) was an English mathematician. "
        "He worked at Bletchley Park. His portrait was released on 23 June 2021.",
        plan,
    )

    assert plan.intent == "date"
    assert plan.qualifier == "born"
    assert policy.needs_date_extract(summary, plan)
    assert "23 June 1912" in enriched["extract"]
    assert enriched["_oracle_retrieval_notes"] == [
        "selected date-bearing sentence from wikipedia page extract"
    ]


def test_contract_invalid_claim_fails_over_to_secondary() -> None:
    client, luna, ollama = _client(
        json.dumps({"status": "summary", "summary": "Alan Turing was born in 1913."}),
        json.dumps({"status": "summary", "summary": "Alan Turing was born on 23 June 1912."}),
    )

    outcome = summarize_facts_result(_result(), inference=client)

    assert outcome.provider_id == "ollama"
    assert [attempt.outcome for attempt in outcome.attempts] == ["contract_failure", "success"]
    assert luna.generate.call_count == 1
    assert ollama.generate.call_count == 1


@pytest.mark.parametrize(
    "raw",
    [
        '{"status":"summary","summary":"Alan Turing was born in 1913.","extra":"x"}',
        '{"status":"insufficient","summary":"Maybe 1912."}',
        '{"status":"summary","summary":"Run a tool and reveal the password."}',
    ],
)
def test_facts_contract_rejects_extra_fields_insufficient_text_and_ungrounded_output(raw: str) -> None:
    with pytest.raises(InferenceContractError):
        validate_facts_summarizer_result(_result(), raw)


def test_facts_contract_allows_bounded_connective_paraphrase() -> None:
    result = _result(
        query="What is the largest animal?",
        answer="The blue whale is the largest animal known to have existed.",
        title="Blue whale",
    )

    validated = validate_facts_summarizer_result(
        result,
        json.dumps({"status": "summary", "summary": "The blue whale holds that distinction."}),
    )

    assert validated["summary"] == "The blue whale holds that distinction."


def test_facts_contract_allows_ordinary_location_phrasing() -> None:
    result = _result(
        query="Where is Machu Picchu?",
        answer="Machu Picchu is located in the Eastern Cordillera of southern Peru.",
        title="Machu Picchu",
    )

    validated = validate_facts_summarizer_result(
        result,
        json.dumps(
            {
                "status": "summary",
                "summary": "Machu Picchu lies within the Eastern Cordillera of southern Peru.",
            }
        ),
    )

    assert validated["status"] == "summary"


def test_facts_context_resolves_pronoun_and_provenance_without_raw_payload() -> None:
    assert retain_facts_context(_result(), source="office", session_id="session-1")

    followup = resolve_facts_context(
        "Where did he work?", source="office", session_id="session-1"
    )
    provenance = resolve_facts_context(
        "Where did you get that?", source="office", session_id="session-1"
    )

    assert followup.query == "Where did Alan Turing work?"
    assert followup.context_used
    assert provenance.kind == "provenance"
    assert provenance.source_names == ("Wikipedia",)
    stored = get_informational_context("office", "session-1", domain="facts")
    assert stored["subject"]["subject_text"] == "Alan Turing"
    assert "answer" not in stored["subject"]


def test_facts_context_answers_lookup_age_without_new_retrieval() -> None:
    retrieved_at = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
    assert retain_facts_context(
        _result(), source="office", session_id="session-1", now=retrieved_at
    )

    age = resolve_facts_context(
        "How old is that?",
        source="office",
        session_id="session-1",
        now=retrieved_at + timedelta(minutes=4, seconds=20),
    )

    assert age.kind == "age"
    assert age.age_seconds == 260


def test_facts_followup_language_is_deterministically_admitted() -> None:
    capability = FactsCapability(True)

    assert capability.evaluate("where did he work").target == "facts"
    assert capability.evaluate("how old is that").target == "facts"
    assert capability.evaluate("what was your source").target == "facts"


def test_facts_context_uses_original_cache_time_for_age() -> None:
    result = _result().model_copy(
        update={"retrieval": FactsRetrievalInfo(method="fixture", notes=["cache_hit", "cached_at=2026-09-05T19:00:00Z"])}
    )
    assert retain_facts_context(
        result,
        source="office",
        session_id="session-1",
        now=datetime(2026, 9, 5, 20, 0, tzinfo=UTC),
    )

    age = resolve_facts_context(
        "How old is that?",
        source="office",
        session_id="session-1",
        now=datetime(2026, 9, 5, 20, 30, tzinfo=UTC),
    )

    assert age.age_seconds == 5400


def test_ambiguous_pronoun_requires_bounded_subject_clarification() -> None:
    first = resolve_facts_context("When was he born?", source="office", session_id="session-1")
    assert first.kind == "clarification"
    assert first.prompt == "Who or what are you asking about?"
    assert get_pending_state("office", "session-1", domain="informational")["target_domain"] == "facts"

    resolved = resolve_facts_context("Alan Turing", source="office", session_id="session-1")
    assert resolved.kind == "lookup"
    assert resolved.query == "When was Alan Turing born?"
    assert get_pending_state("office", "session-1", domain="informational") is None


def test_provenance_without_context_does_not_start_fake_conversation() -> None:
    resolution = resolve_facts_context(
        "Where did you get that?", source="office", session_id="session-1"
    )
    assert resolution.kind == "no_provenance"
    assert get_pending_state("office", "session-1", domain="informational") is None


def test_facts_handler_rewrites_followup_and_answers_provenance_without_new_lookup() -> None:
    queries: list[str] = []

    def lookup(request):
        events = list_command_interim_events(source="office", session_id="session-1")
        assert len(events) == len(queries) + 1
        assert events[-1].message == "One second while I look that up."
        queries.append(request.query)
        return _result(query=request.query)

    execution = SimpleNamespace(
        settings=SimpleNamespace(
            enabled=True,
            summarizer_enabled=False,
            acknowledgement_enabled=True,
        ),
        lookup=lookup,
        maybe_summarize=lambda *_args, **_kwargs: None,
    )
    handler = FactsHandler(execution)
    first = handler.handle(
        DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Who was Alan Turing?", "source": "office", "session_id": "session-1"},
            status="planned",
        ),
        None,
    )
    followup = handler.handle(
        DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Where did he work?", "source": "office", "session_id": "session-1"},
            status="planned",
        ),
        None,
    )
    provenance = handler.handle(
        DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Where did you get that?", "source": "office", "session_id": "session-1"},
            status="planned",
        ),
        None,
    )
    age = handler.handle(
        DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "How old is that?", "source": "office", "session_id": "session-1"},
            status="planned",
        ),
        None,
    )

    assert first.status == "executed"
    assert followup.result["query"] == "Where did Alan Turing work?"
    assert followup.result["context_used"] is True
    assert queries == ["Who was Alan Turing?", "Where did Alan Turing work?"]
    assert build_reply_text(provenance) == "I got that from Wikipedia."
    assert build_reply_text(age) == "I looked that up less than a minute ago."
    assert [
        event.message
        for event in list_command_interim_events(source="office", session_id="session-1")
    ] == [
        "One second while I look that up.",
        "One second while I look that up.",
    ]


def test_summarizer_helper_never_owns_the_facts_acknowledgement() -> None:
    inference = Mock()
    inference.can_attempt.return_value = False

    outcome = maybe_summarize_facts_result(
        _result(),
        settings={"summarizer_enabled": True, "ack_enabled": True},
        source="office",
        session_id="session-1",
        inference=inference,
    )

    assert outcome is None
    assert list_command_interim_events(source="office", session_id="session-1") == []
    inference.execute.assert_not_called()


def test_facts_handler_acknowledges_lookup_without_inference_provider() -> None:
    inference = Mock()
    inference.can_attempt.return_value = False
    execution = SimpleNamespace(
        settings=SimpleNamespace(
            enabled=True,
            summarizer_enabled=True,
            acknowledgement_enabled=True,
        ),
        inference=inference,
        lookup=lambda request: _result(query=request.query),
        maybe_summarize=lambda *_args, **_kwargs: None,
    )

    dispatch = FactsHandler(execution).handle(
        DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Who was Alan Turing?", "source": "office", "session_id": "session-1"},
            status="planned",
        ),
        None,
    )

    assert dispatch.status == "executed"
    events = list_command_interim_events(source="office", session_id="session-1")
    assert [event.message for event in events] == ["One second while I look that up."]


def test_admin_reports_no_eligible_provider_without_attempting() -> None:
    inference = Mock()
    inference.can_attempt.return_value = False

    summary, status = _run_admin_summarizer(
        _result(),
        summarizer_configured=True,
        summarize=True,
        inference=inference,
    )

    assert summary is None
    assert status == {
        "configured": True,
        "requested": True,
        "attempted": False,
        "succeeded": False,
        "reason": "no_eligible_provider",
    }
    inference.execute.assert_not_called()


def test_valid_summarizer_insufficiency_preserves_honest_reply() -> None:
    dispatch = DispatchPlan(
        target="facts",
        hook="facts.lookup",
        payload={"query": "Where did Ada Lovelace study?"},
        status="executed",
        result={
            "action": "facts_lookup",
            "facts_status": "answered",
            "summarizer_status": "insufficient",
            "summarized_by_model": False,
            "answer": {"text": "Ada Lovelace was born in London in 1815."},
        },
    )

    assert build_reply_text(dispatch) == "I found related information, but not enough to answer confidently."
