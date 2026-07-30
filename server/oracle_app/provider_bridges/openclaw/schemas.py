from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OpenClawBridgeOptions(BaseModel):
    adapter: Literal["http", "mock", "websocket", "ssh_cli"] = "http"
    base_url: str = ""
    endpoint_path: str = ""
    timeout_seconds: int = Field(default=20, ge=1, le=86400)
    max_suggestions: int = Field(default=10, ge=1, le=100)
    use_mock: bool = False
    ssh_target: str = ""
    ssh_password: str = ""
    ssh_identity_file: str = ""
    ssh_connect_timeout_seconds: int = Field(default=8, ge=1, le=60)
    cli_path: str = "openclaw"
    cli_mode: Literal["agent", "infer"] = "agent"
    agent_name: str = "oracle-advisor"
    model: str = "ollama/gpt-oss:20b"
    start_gateway: bool = False
    gateway_port: int = Field(default=18789, ge=1, le=65535)


class NormalizedOpenClawSuggestion(BaseModel):
    title: str
    severity: str
    category: str
    source: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    suggested_action: str
    recommended_oracle_action: str | None = None
    confidence: float = 0.0
    requires_review: bool = True


class OpenClawBridgeResult(BaseModel):
    ok: bool
    provider: str = "openclaw"
    adapter: str
    raw_response: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    mock: bool = False
