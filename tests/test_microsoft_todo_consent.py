from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


PATH = Path(__file__).resolve().parents[1] / "scripts" / "microsoft-todo-consent.py"
SPEC = importlib.util.spec_from_file_location("microsoft_todo_consent", PATH)
assert SPEC is not None and SPEC.loader is not None
consent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consent)


def test_personal_device_flow_returns_refresh_credential_without_printing_it(monkeypatch, capsys) -> None:
    answers = iter([
        {"device_code": "device-secret", "user_code": "ABC-123", "verification_uri": "https://www.microsoft.com/devicelogin", "expires_in": 300, "interval": 5},
        {"error": "authorization_pending"},
        {"access_token": "access-secret", "refresh_token": "refresh-secret"},
    ])
    calls = []
    def post(url, fields, timeout):
        calls.append((url, fields))
        return next(answers)
    monkeypatch.setattr(consent, "_post", post)
    monkeypatch.setattr(consent.time, "sleep", lambda seconds: None)
    assert consent._consent("11111111-1111-1111-1111-111111111111", "consumers") == "refresh-secret"
    output = capsys.readouterr().out
    assert "ABC-123" in output
    assert "access-secret" not in output and "refresh-secret" not in output
    assert calls[0][0].endswith("/consumers/oauth2/v2.0/devicecode")
    assert calls[0][1]["scope"] == "Tasks.ReadWrite offline_access"


def test_helper_passes_refresh_only_on_stdin_to_canonical_secret_cli(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(consent, "_consent", lambda *args: "refresh-secret")
    captured = []
    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(consent.subprocess, "run", run)
    status = consent.main([
        "--tenant", "consumers", "--client-id", "11111111-1111-1111-1111-111111111111",
        "--logical-id", "TODO_REFRESH", "--expected-secret-generation", "generation-1",
        "--socket", str(tmp_path / "control.sock"),
    ])
    assert status == 0
    argv, kwargs = captured[0]
    assert "--value-stdin" in argv
    assert "refresh-secret" not in " ".join(argv)
    assert kwargs["input"] == "refresh-secret\n"
    assert "refresh-secret" not in capsys.readouterr().out
