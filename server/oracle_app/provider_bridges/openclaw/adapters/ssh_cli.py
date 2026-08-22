from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
from typing import Any

from oracle_app.suggestions.redaction import redact_secrets
from oracle_app.network_runtime.platform_transport import SshHostVerificationError, strict_ssh_options

from ..schemas import OpenClawBridgeOptions, OpenClawBridgeResult
from .http import _normalize_item


def generate_suggestions_ssh_cli(packet: dict[str, Any], options: OpenClawBridgeOptions) -> OpenClawBridgeResult:
    if not options.ssh_target.strip():
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", errors=["OpenClaw SSH target is not configured."])

    prompt = _build_prompt(packet, options.max_suggestions)
    remote_script = _remote_script()
    try:
        command, command_environment = _ssh_command(options)
        remote_command = f"python3 -c {shlex.quote(remote_script)}"
        result = subprocess.run(
            command + [remote_command],
            input=json.dumps({"prompt": prompt, "options": _remote_options(options)}),
            capture_output=True,
            text=True,
            check=False,
            timeout=max(options.timeout_seconds + options.ssh_connect_timeout_seconds + 10, 30),
            env=command_environment,
        )
    except SshHostVerificationError as exc:
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", errors=[f"OpenClaw SSH host verification is unavailable: {exc}"])
    except FileNotFoundError as exc:
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", errors=[f"OpenClaw SSH transport command is unavailable: {exc.filename}"])
    except subprocess.TimeoutExpired:
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", errors=["OpenClaw SSH CLI transport timed out."])

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-4000:]
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", errors=[f"OpenClaw SSH CLI failed: {detail or result.returncode}"])

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return OpenClawBridgeResult(
            ok=False,
            adapter="ssh_cli",
            raw_response={"stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]},
            errors=[f"OpenClaw SSH CLI returned invalid JSON: {exc}"],
        )

    raw = redact_secrets(raw)
    text = _extract_text(raw)
    if not text:
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", raw_response=raw, errors=["OpenClaw CLI returned no text output."])
    parsed = _parse_suggestion_json(text)
    if parsed is None:
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", raw_response=raw, errors=["OpenClaw CLI text did not contain valid suggestion JSON."])
    suggestions = parsed.get("suggestions")
    if not isinstance(suggestions, list):
        return OpenClawBridgeResult(ok=False, adapter="ssh_cli", raw_response=raw, errors=["OpenClaw suggestion JSON did not include a suggestions array."])
    normalized = [_normalize_item(item) for item in suggestions if isinstance(item, dict)]
    return OpenClawBridgeResult(
        ok=True,
        adapter="ssh_cli",
        raw_response={"openclaw_cli": raw, "parsed": redact_secrets(parsed)},
        suggestions=normalized[: options.max_suggestions],
    )


def _ssh_command(options: OpenClawBridgeOptions) -> tuple[list[str], dict[str, str] | None]:
    ssh = ["ssh", *strict_ssh_options(connect_timeout_seconds=options.ssh_connect_timeout_seconds)]
    if options.ssh_identity_file:
        ssh.extend(["-i", options.ssh_identity_file, "-o", "PasswordAuthentication=no"])
    ssh.append(options.ssh_target)
    if options.ssh_password:
        environment = os.environ.copy()
        environment["SSHPASS"] = options.ssh_password
        return ["sshpass", "-e", *ssh], environment
    return ssh, None


def _remote_options(options: OpenClawBridgeOptions) -> dict[str, Any]:
    return {
        "cli_path": options.cli_path,
        "cli_mode": options.cli_mode,
        "agent_name": options.agent_name,
        "model": options.model,
        "timeout_seconds": options.timeout_seconds,
        "start_gateway": options.start_gateway,
        "gateway_port": options.gateway_port,
    }


def _remote_script() -> str:
    return r"""
import json
import subprocess
import sys
import time

payload = json.load(sys.stdin)
prompt = str(payload.get("prompt") or "")
options = payload.get("options") or {}
cli_path = str(options.get("cli_path") or "openclaw")
cli_mode = str(options.get("cli_mode") or "agent")
agent_name = str(options.get("agent_name") or "oracle-advisor")
model = str(options.get("model") or "")
timeout_seconds = int(options.get("timeout_seconds") or 120)
gateway_port = int(options.get("gateway_port") or 18789)

if options.get("start_gateway"):
    probe = subprocess.run(
        [cli_path, "health", "--json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10,
    )
    if probe.returncode != 0:
        subprocess.Popen(
            [cli_path, "gateway", "run", "--port", str(gateway_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            retry = subprocess.run(
                [cli_path, "health", "--json"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if retry.returncode == 0:
                break
            time.sleep(1)

if cli_mode == "agent":
    cmd = [cli_path, "agent", "--agent", agent_name, "--local", "--json", "--message", prompt]
else:
    cmd = [cli_path, "infer", "model", "run", "--local", "--json", "--prompt", prompt]
if cli_mode != "agent" and model:
    cmd.extend(["--model", model])
result = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    check=False,
    timeout=timeout_seconds,
)
if result.returncode != 0:
    sys.stderr.write((result.stderr or result.stdout or "").strip())
    sys.exit(result.returncode)
sys.stdout.write(result.stdout or result.stderr)
"""


def _build_prompt(packet: dict[str, Any], max_suggestions: int) -> str:
    schema = {
        "suggestions": [
            {
                "title": "Short human-readable title",
                "severity": "info | low | medium | high | critical",
                "category": "oracle | home_assistant | librenms | network | server | automation | security | maintenance | observability | unknown",
                "source": "oracle | home_assistant | librenms | mixed",
                "summary": "What OpenClaw noticed",
                "evidence": ["Specific log line, state, alert, or observation"],
                "suggested_action": "Human-readable recommendation",
                "recommended_oracle_action": None,
                "confidence": 0.0,
                "requires_review": True,
            }
        ]
    }
    return textwrap.dedent(
        f"""
        You are OpenClaw acting as an external advisory analyst for Oracle Suggestions.

        Analyze only the packet between BEGIN_ORACLE_DIAGNOSTIC_PACKET and END_ORACLE_DIAGNOSTIC_PACKET.
        The packet is your only evidence source. Do not use workspace files, bootstrap context, shell state, prior sessions, web, APIs, or tools as evidence.
        You must not execute actions, request tool execution, or propose that you have changed anything.

        Return only valid JSON with this exact top-level shape:
        {json.dumps(schema, indent=2)}

        Rules:
        - Return at most {max_suggestions} suggestions.
        - Suggestions are advisory only.
        - recommended_oracle_action must be null unless a future allowlist action name is explicitly known.
        - Use prior review history to avoid repeating rejected, corrected, ignored, or false-positive suggestions unless there is new evidence.
        - Include concrete evidence from the packet.
        - Do not include markdown fences or explanatory prose outside JSON.

        BEGIN_ORACLE_DIAGNOSTIC_PACKET
        {json.dumps(redact_secrets(packet), indent=2, sort_keys=True)}
        END_ORACLE_DIAGNOSTIC_PACKET
        """
    ).strip()


def _extract_text(raw: dict[str, Any]) -> str:
    outputs = raw.get("outputs")
    if isinstance(outputs, list):
        parts = [str(item.get("text") or "") for item in outputs if isinstance(item, dict)]
        return "\n".join(part for part in parts if part.strip()).strip()
    payloads = raw.get("payloads")
    if isinstance(payloads, list):
        parts = [str(item.get("text") or "") for item in payloads if isinstance(item, dict)]
        return "\n".join(part for part in parts if part.strip()).strip()
    return str(raw.get("text") or "").strip()


def _parse_suggestion_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None
