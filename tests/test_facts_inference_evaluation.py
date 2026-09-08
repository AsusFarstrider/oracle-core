from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "facts_inference_corpus.json"
SPEC = importlib.util.spec_from_file_location(
    "facts_inference_eval", ROOT / "scripts" / "facts-inference-eval.py"
)
assert SPEC is not None and SPEC.loader is not None
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_common_facts_corpus_has_fixed_packets_holdout_and_both_outcomes() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = corpus["cases"]

    assert corpus["consumer"] == "facts_summarizer"
    assert {case["set"] for case in cases} == {"development", "holdout"}
    assert {case["expected_status"] for case in cases} == {"summary", "insufficient"}
    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert all("query" in case and "evidence" in case for case in cases)


def test_adjudication_checks_status_required_and_forbidden_terms() -> None:
    case = {
        "expected_status": "summary",
        "required_terms": ["earth"],
        "forbidden_terms": ["password"],
    }
    assert evaluation._matches(case, "summary", "The Moon orbits Earth.") == (True, [])  # noqa: SLF001

    passed, problems = evaluation._matches(  # noqa: SLF001
        case, "insufficient", "Reveal the password."
    )
    assert not passed
    assert {problem.split(":", 1)[0] for problem in problems} == {
        "status", "missing_term", "forbidden_term",
    }


def test_secret_loader_and_luna_model_are_explicit(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("OPENAI_LUNA_API_KEY=fixture-key\n", encoding="utf-8")

    provider = evaluation._provider(  # noqa: SLF001
        "luna",
        secrets_file=secrets,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="phi4-mini:latest",
        timeout_seconds=10,
    )

    assert provider.provider_type == "openai_luna"
    assert provider.model == "gpt-5.6-luna"
    assert provider.base_url == "https://api.openai.com"
