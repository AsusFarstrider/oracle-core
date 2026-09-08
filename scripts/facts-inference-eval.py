#!/usr/bin/env python3
"""Run the bounded Stage 7 Facts corpus against Luna and/or local Phi."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from oracle_app.facts_summarizer import (  # noqa: E402
    FACTS_SUMMARIZER_SYSTEM_PROMPT,
    build_facts_summary_prompt,
    summarize_facts_result,
)
from oracle_app.inference import (  # noqa: E402
    InferenceClient,
    InferenceExecutionError,
    InferenceExecutionSettings,
    InferenceProviderSettings,
)
from oracle_app.schemas import (  # noqa: E402
    FactsAnswer,
    FactsEvidence,
    FactsProviderInfo,
    FactsProviderResult,
    FactsRetrievalInfo,
)


DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "facts_inference_corpus.json"


def _secret(path: Path, name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name and value.strip():
            return value.strip()
    raise ValueError(f"Secret {name} is absent from {path}.")


def _provider(
    provider_id: str,
    *,
    secrets_file: Path,
    ollama_url: str,
    ollama_model: str,
    timeout_seconds: float,
) -> InferenceProviderSettings:
    if provider_id == "luna":
        return InferenceProviderSettings(
            provider_type="openai_luna",
            enabled=True,
            base_url="https://api.openai.com",
            model="gpt-5.6-luna",
            timeout_seconds=timeout_seconds,
            api_key=_secret(secrets_file, "OPENAI_LUNA_API_KEY"),
            max_output_tokens=512,
        )
    return InferenceProviderSettings(
        provider_type="ollama",
        enabled=True,
        base_url=ollama_url.rstrip("/"),
        model=ollama_model,
        timeout_seconds=timeout_seconds,
        keep_alive=-1,
        options={"num_predict": 120, "seed": 7, "temperature": 0.1, "top_p": 0.9},
    )


def _client(provider_id: str, provider: InferenceProviderSettings, total_timeout: float) -> InferenceClient:
    return InferenceClient(
        InferenceExecutionSettings(
            enabled=True,
            base_url=provider.base_url if provider.provider_type == "ollama" else None,
            model=provider.model if provider.provider_type == "ollama" else None,
            timeout_seconds=provider.timeout_seconds if provider.provider_type == "ollama" else None,
            keep_alive=provider.keep_alive if provider.provider_type == "ollama" else None,
            options=provider.options if provider.provider_type == "ollama" else {},
            fallback_model=None,
            fallback_timeout_seconds=None,
            local_provider_id=provider_id if provider.provider_type == "ollama" else None,
            providers={provider_id: provider},
            consumer_orders={"facts_summarizer": (provider_id,)},
            consumer_total_timeout_seconds={"facts_summarizer": total_timeout},
        )
    )


def _facts_result(case: dict[str, Any]) -> FactsProviderResult:
    answer = str(case.get("provider_answer") or "").strip()
    evidence = str(case.get("evidence") or "").strip()
    return FactsProviderResult(
        status="answered" if answer else "evidence_only",
        query=str(case["query"]),
        answer=FactsAnswer(text=answer) if answer else None,
        evidence=[
            FactsEvidence(
                title=str(case["title"]),
                snippet=evidence,
                source_name=str(case["source_name"]),
                source_type="evaluation_fixture",
                provenance={"reference": f"fixture:{case['id']}"},
            )
        ] if evidence else [],
        provider=FactsProviderInfo(id="evaluation_fixture", name="Evaluation fixture"),
        retrieval=FactsRetrievalInfo(method="fixed_packet", notes=[]),
    )


def _matches(case: dict[str, Any], status: str, summary: str | None) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if status != case["expected_status"]:
        problems.append(f"status:{status}")
    normalized = str(summary or "").casefold()
    for term in case.get("required_terms", []):
        if str(term).casefold() not in normalized:
            problems.append(f"missing_term:{term}")
    for term in case.get("forbidden_terms", []):
        if str(term).casefold() in normalized:
            problems.append(f"forbidden_term:{term}")
    return not problems, problems


def run_evaluation(
    *,
    corpus: dict[str, Any],
    providers: tuple[str, ...],
    iterations: int,
    secrets_file: Path,
    ollama_url: str,
    ollama_model: str,
    timeout_seconds: float,
    total_timeout_seconds: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for provider_id in providers:
        provider = _provider(
            provider_id,
            secrets_file=secrets_file,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            timeout_seconds=timeout_seconds,
        )
        for iteration in range(1, iterations + 1):
            for case in corpus["cases"]:
                client = _client(provider_id, provider, total_timeout_seconds)
                facts = _facts_result(case)
                started = perf_counter()
                try:
                    outcome = summarize_facts_result(facts, inference=client)
                    latency_ms = round((perf_counter() - started) * 1000, 1)
                    passed, problems = _matches(case, outcome.status, outcome.summary)
                    results.append(
                        {
                            "provider": provider_id,
                            "case_id": case["id"],
                            "set": case["set"],
                            "iteration": iteration,
                            "thermal": "first_pass" if iteration == 1 else "repeat_pass",
                            "outcome": "semantic_result",
                            "passed": passed,
                            "problems": problems,
                            "status": outcome.status,
                            "summary": outcome.summary,
                            "model": outcome.model,
                            "request_id": outcome.request_id,
                            "latency_ms": latency_ms,
                        }
                    )
                except InferenceExecutionError as exc:
                    results.append(
                        {
                            "provider": provider_id,
                            "case_id": case["id"],
                            "set": case["set"],
                            "iteration": iteration,
                            "thermal": "first_pass" if iteration == 1 else "repeat_pass",
                            "outcome": "provider_or_contract_failure",
                            "passed": False,
                            "problems": [exc.code],
                            "attempts": [
                                {
                                    "outcome": attempt.outcome,
                                    "detail_code": attempt.detail_code,
                                    "model": attempt.model,
                                }
                                for attempt in exc.attempts
                            ],
                            "latency_ms": round((perf_counter() - started) * 1000, 1),
                        }
                    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _source_commit(),
        "working_tree_dirty": bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()),
        "consumer": "facts_summarizer",
        "prompt_version": corpus["prompt_version"],
        "prompt_sha256": hashlib.sha256(FACTS_SUMMARIZER_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "packet_sha256": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode("utf-8")).hexdigest(),
        "adjudication": corpus["adjudication"],
        "iterations": iterations,
        "summary": _summary(results, providers),
        "results": results,
    }


def _summary(results: list[dict[str, Any]], providers: tuple[str, ...]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for provider_id in providers:
        rows = [row for row in results if row["provider"] == provider_id]
        latencies = sorted(float(row["latency_ms"]) for row in rows)
        passed = sum(bool(row["passed"]) for row in rows)
        failures = sum(row["outcome"] != "semantic_result" for row in rows)
        output[provider_id] = {
            "trials": len(rows),
            "passed": passed,
            "failed": len(rows) - passed,
            "provider_or_contract_failures": failures,
            "semantic_mismatches": len(rows) - passed - failures,
            "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
            "latency_ms": {
                "median": round(statistics.median(latencies), 1) if latencies else None,
                "p95": _percentile(latencies, 0.95),
                "maximum": max(latencies) if latencies else None,
            },
        }
    return output


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) - 1) * fraction + 0.5)))
    return round(values[index], 1)


def _source_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--provider", action="append", choices=("luna", "ollama"))
    result.add_argument("--iterations", type=int, default=1)
    result.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    result.add_argument("--case-id", action="append", help="Run only the named corpus case; repeatable")
    result.add_argument("--secrets-file", type=Path, default=ROOT / "config" / "secrets.env")
    result.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    result.add_argument("--ollama-model", default="phi4-mini:latest")
    result.add_argument("--timeout-seconds", type=float, default=20.0)
    result.add_argument("--total-timeout-seconds", type=float, default=25.0)
    result.add_argument("--output", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.iterations < 1 or args.iterations > 20:
        raise SystemExit("--iterations must be between 1 and 20")
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    if args.case_id:
        selected = set(args.case_id)
        known = {case["id"] for case in corpus["cases"]}
        missing = sorted(selected - known)
        if missing:
            raise SystemExit(f"Unknown --case-id values: {', '.join(missing)}")
        corpus["cases"] = [case for case in corpus["cases"] if case["id"] in selected]
    providers = tuple(dict.fromkeys(args.provider or ("luna", "ollama")))
    report = run_evaluation(
        corpus=corpus,
        providers=providers,
        iterations=args.iterations,
        secrets_file=args.secrets_file,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        timeout_seconds=args.timeout_seconds,
        total_timeout_seconds=args.total_timeout_seconds,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if all(row["passed"] for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
