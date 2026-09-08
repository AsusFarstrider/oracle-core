from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "fallback_inference_corpus.json"
UTTERANCE_LEDGER = ROOT / "tests" / "fixtures" / "utterance_ledger.json"
CAPABILITY_MATRIX = ROOT / "tests" / "fixtures" / "utterance_capability_matrix.json"
SPEC = importlib.util.spec_from_file_location(
    "fallback_inference_eval", ROOT / "scripts" / "fallback-inference-eval.py"
)
assert SPEC is not None and SPEC.loader is not None
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_common_corpus_has_development_holdout_and_all_semantic_outcomes() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = corpus["cases"]

    assert corpus["consumer"] == "fallback_router"
    assert {case["set"] for case in cases} == {"development", "holdout"}
    assert {case["expected_status"] for case in cases} == {
        "resolved",
        "unresolved",
        "unsupported",
    }
    assert len({case["id"] for case in cases}) == len(cases)


def test_common_corpus_carries_production_garbage_and_genuine_noisy_rescue() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}

    production_inputs = {
        "or",
        "it worked",
        "oh",
        "gold",
        "alright, cool",
        "ugh",
        "Yeah.",
        "Oracle doesn't care",
    }
    production_cases = [
        case for case in cases.values() if case.get("provenance") == "retained-production-false-wake"
    ]
    assert {case["input"] for case in production_cases} == production_inputs
    assert all(case["set"] == "holdout" for case in production_cases)
    assert all(case["allowed_statuses"] == ["unresolved", "unsupported"] for case in production_cases)
    assert all(case["expected_domain"] == "" for case in production_cases)

    noisy_rescue = cases["whisper-like-calendar-noise"]
    assert noisy_rescue["expected_status"] == "resolved"
    assert noisy_rescue["expected_domain"] == "calendar"


def test_production_garbage_is_not_promoted_to_deterministic_utterance_authority() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    garbage = {
        " ".join(str(case["input"]).casefold().split())
        for case in corpus["cases"]
        if case.get("provenance") == "retained-production-false-wake"
    }
    deterministic_inputs: set[str] = set()
    for path in (UTTERANCE_LEDGER, CAPABILITY_MATRIX):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("cases", payload.get("utterances", []))
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("utterance", "input", "text"):
                value = row.get(key)
                if isinstance(value, str):
                    deterministic_inputs.add(" ".join(value.casefold().split()))

    assert garbage.isdisjoint(deterministic_inputs)


def test_adjudication_checks_status_domain_identity_and_required_terms() -> None:
    case = {
        "expected_status": "resolved",
        "expected_domain": "calendar",
        "expected_user_id": "alex",
        "required_terms": ["tomorrow", "5:30"],
    }
    passed, problems = evaluation._matches(  # noqa: SLF001 - evaluation contract test
        case,
        {
            "status": "resolved",
            "domain": "calendar",
            "normalized_text": "alex calendar tomorrow at 5:30",
            "user_id": "alex",
        },
    )
    assert passed
    assert problems == []

    passed, problems = evaluation._matches(  # noqa: SLF001 - evaluation contract test
        case,
        {
            "status": "resolved",
            "domain": "facts",
            "normalized_text": "alex calendar today",
            "user_id": "someone_else",
        },
    )
    assert not passed
    assert {problem.split(":", 1)[0] for problem in problems} == {
        "domain",
        "user_id",
        "missing_term",
    }


def test_adjudication_allows_only_declared_terminal_garbage_outcomes() -> None:
    case = {
        "expected_status": "unresolved",
        "allowed_statuses": ["unresolved", "unsupported"],
        "expected_domain": "",
    }
    for status in ("unresolved", "unsupported"):
        passed, problems = evaluation._matches(  # noqa: SLF001 - evaluation contract test
            case,
            {"status": status, "domain": "", "normalized_text": "", "user_id": ""},
        )
        assert passed
        assert problems == []

    passed, problems = evaluation._matches(  # noqa: SLF001 - evaluation contract test
        case,
        {
            "status": "resolved",
            "domain": "facts",
            "normalized_text": "tell me about gold",
            "user_id": "",
        },
    )
    assert not passed
    assert {problem.split(":", 1)[0] for problem in problems} == {"status", "domain"}


def test_secret_loader_selects_named_value_without_ambient_discovery(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("OTHER=value\nOPENAI_LUNA_API_KEY=fixture-key\n", encoding="utf-8")

    assert evaluation._secret(secrets, "OPENAI_LUNA_API_KEY") == "fixture-key"  # noqa: SLF001


def test_luna_evaluation_is_fixed_to_the_approved_model(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("OPENAI_LUNA_API_KEY=fixture-key\n", encoding="utf-8")

    provider = evaluation._provider(  # noqa: SLF001 - evaluation contract test
        "luna",
        secrets_file=secrets,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="phi4-mini:latest",
        timeout_seconds=10,
    )

    assert provider.provider_type == "openai_luna"
    assert provider.model == "gpt-5.6-luna"
    assert provider.base_url == "https://api.openai.com"
