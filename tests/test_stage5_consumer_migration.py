from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMOTED_CONSUMERS = (
    "satellite/pi_runtime/oracle_client.py",
    "satellite/pi_runtime/alerts_runtime.py",
    "satellite/pi_runtime/local_control.py",
    "satellite/pi_runtime/request_runtime.py",
    "satellite/pi_runtime/reply_runtime.py",
    "satellite/pi_runtime/pipeline_runtime.py",
    "satellite/pc_push_to_talk.py",
    "satellite_ui/app.js",
    "ui/app.js",
    "ui/system.js",
    "house_ui/app.js",
    "scripts/oracle-admin.py",
    "scripts/test-brain.sh",
)
PRIVATE_CLIENTS = (
    "mobile-client/App.tsx",
    "mobile-client/src/lib/oracleApi.ts",
    "mobile-client/src/types.ts",
    "scripts/stt_benchmark.py",
)


def _contents() -> dict[str, str]:
    content = {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in PROMOTED_CONSUMERS
    }
    content.update(
        {
            relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
            for relative in PRIVATE_CLIENTS
            if (REPO_ROOT / relative).is_file()
        }
    )
    return content


def test_controlled_consumers_have_no_obsolete_route_literals() -> None:
    obsolete = {
        '"/' + 'command"', "'/" + "command'", "`/" + "command`", "}/" + "command",
        '"/' + 'stt"', "'/" + "stt'", "`/" + "stt`", "}/" + "stt",
        '"/' + 'tts"', "'/" + "tts'", "`/" + "tts`", "}/" + "tts",
        "/alerts/" + "pending",
        "/api/" + "voice/",
        "/api/" + "satellites/config",
        "/audiobooks/" + "stream/",
    }

    for relative, content in _contents().items():
        for value in obsolete:
            assert value not in content, f"{relative} still consumes {value}"


def test_controlled_consumers_do_not_read_retired_public_response_shapes() -> None:
    retired_shape_reads = (
        "response." + "dispatch",
        "response?." + "dispatch",
        "raw_response.get(\"" + "dispatch" + "\")",
        '["' + "dispatch" + '"]',
        "response." + "route.target",
        "response?." + "route?.target",
    )

    for relative, content in _contents().items():
        for value in retired_shape_reads:
            assert value not in content, f"{relative} still reads retired shape {value}"


def test_managed_client_families_reference_canonical_contracts() -> None:
    content = _contents()

    assert "/api/conversation/command" in content["satellite/pi_runtime/oracle_client.py"]
    assert "/api/speech/stt" in content["satellite/pi_runtime/oracle_client.py"]
    assert "/api/satellite/alerts/claim" in content["satellite/pi_runtime/oracle_client.py"]
    assert "continuation_token" in content["satellite_ui/app.js"]
    if "mobile-client/src/types.ts" in content:
        assert "ConversationEffects" in content["mobile-client/src/types.ts"]
    assert "_poll_pending_alerts" not in content["satellite/pc_push_to_talk.py"]


def test_private_stt_benchmark_uses_installed_canonical_brain_only() -> None:
    benchmark = _contents().get("scripts/stt_benchmark.py")
    if benchmark is None:
        return

    assert "/api/speech/stt" in benchmark
    assert "/api/admin/health" in benchmark
    assert "--base-url" in benchmark
    assert "get_stt_provider" not in benchmark
    assert "get_stt_settings" not in benchmark
    assert "uvicorn" not in benchmark
