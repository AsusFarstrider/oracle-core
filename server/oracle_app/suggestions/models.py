from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    reason: str | None = Field(default=None, max_length=500)
    custom_prompt: str | None = Field(default=None, max_length=4000)
    window_start: str | None = Field(default=None, max_length=64)
    window_end: str | None = Field(default=None, max_length=64)
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

    @model_validator(mode="after")
    def validate_review_evidence(self) -> SuggestionReviewRequest:
        if self.status in {"rejected", "false_positive"} and not str(
            self.rejection_reason or ""
        ).strip():
            raise ValueError(f"{self.status} review requires a rejection reason")
        if self.status == "corrected" and not str(self.correction_text or "").strip():
            raise ValueError("corrected review requires correction text")
        if self.suppress_if_repeated and self.status not in {
            "rejected", "corrected", "ignored", "false_positive"
        }:
            raise ValueError("repeat suppression requires a negative or corrective review")
        return self


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
        "observability",
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
