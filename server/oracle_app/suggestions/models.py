from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SuggestionStatus = Literal[
    "new",
    "accepted",
    "rejected",
    "corrected",
    "ignored",
    "archived",
    "needs_more_data",
    "false_positive",
]

RunType = Literal[
    "all_sources",
    "oracle",
    "home_assistant",
    "librenms",
    "custom",
]


class SuggestionGenerateRequest(BaseModel):
    run_type: RunType = "all_sources"
    reason: str | None = None
    custom_prompt: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    max_suggestions: int | None = Field(default=None, ge=1, le=100)
    use_mock: bool = False
    wait_for_completion: bool = False


class SuggestionReviewRequest(BaseModel):
    status: SuggestionStatus
    notes: str | None = None
    correction_text: str | None = None
    rejection_reason: str | None = None
    future_automation_candidate: bool = False
    suppress_if_repeated: bool = False


class OpenClawSuggestionItem(BaseModel):
    title: str = Field(..., min_length=1)
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    category: Literal[
        "oracle",
        "home_assistant",
        "librenms",
        "network",
        "server",
        "automation",
        "security",
        "maintenance",
        "unknown",
    ] = "unknown"
    source: Literal["oracle", "home_assistant", "librenms", "mixed"] = "mixed"
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    recommended_oracle_action: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_review: bool = True


class OpenClawResponse(BaseModel):
    suggestions: list[OpenClawSuggestionItem] = Field(default_factory=list)
    model: str | None = None
    notes: str | None = None
