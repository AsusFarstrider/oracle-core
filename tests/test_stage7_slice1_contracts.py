from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_shared_inference_contract_ratifies_semantic_failover_boundary() -> None:
    contract = _read("docs/contracts/ollama-policy.md")

    for outcome in ("`resolved`", "`unresolved`", "`unsupported`"):
        assert outcome in contract
    assert "fallback `unresolved` does not try another provider" in contract
    assert "fallback `unsupported` does not try another provider" in contract
    assert "Facts `insufficient` does not try another provider" in contract
    assert "operational failure or violates the consumer contract" in contract
    assert "Credential presence never enables cloud inference" in contract


def test_stage7_contract_preserves_bounded_consumer_dispositions() -> None:
    inference = _read("docs/contracts/ollama-policy.md")
    facts = _read("docs/contracts/facts.md")

    assert "Music's existing Ollama-assisted" in inference
    assert "remain local-\nOllama compatibility consumers" in inference
    assert "Suggestions is a separate opt-in advisory contract" in inference
    assert "direct OpenAI/Luna or OpenClaw/local-model execution" in inference
    assert "Stage 7 remains evidence-only" in facts
    assert "An absent/disabled Facts capability remains disabled" in facts
