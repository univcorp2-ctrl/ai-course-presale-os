from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskKind(StrEnum):
    TRIAGE = "triage"
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    CODE_EXAMPLE = "code_example"
    FACT_CHECK = "fact_check"
    PEDAGOGY = "pedagogy"
    COMPLIANCE = "compliance"
    MARKETING = "marketing"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SourceDocument(BaseModel):
    id: str
    source_type: str
    title: str
    text: str
    url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        normalized = re.sub(r"\s+", " ", f"{self.url or ''}\n{self.title}\n{self.text[:3000]}")
        return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()


class ReviewFinding(BaseModel):
    code: str
    severity: Severity
    message: str
    suggestion: str | None = None
    evidence: str | None = None


class AgentReview(BaseModel):
    provider_id: str
    model: str
    task: TaskKind
    approved: bool
    score: int = Field(ge=0, le=100)
    findings: list[ReviewFinding] = Field(default_factory=list)
    review_eligible: bool = True
    raw_excerpt: str = ""


class ContentDraft(BaseModel):
    release_id: str
    slug: str
    title: str
    audience: str
    body_markdown: str
    source_labels: dict[str, str] = Field(default_factory=dict)
    model_trace: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    def content_hash(self) -> str:
        return hashlib.sha256(self.body_markdown.encode("utf-8")).hexdigest()


class GateResult(BaseModel):
    passed: bool
    blockers: list[ReviewFinding] = Field(default_factory=list)
    warnings: list[ReviewFinding] = Field(default_factory=list)
    distinct_review_providers: int = 0


class ReleaseManifest(BaseModel):
    release_id: str
    slug: str
    content_hash: str
    status: Literal["draft", "approved", "published", "rejected"] = "draft"
    gate_passed: bool = False
    legal_complete: bool = False
    deterministic_findings: list[ReviewFinding] = Field(default_factory=list)
    agent_reviews: list[AgentReview] = Field(default_factory=list)
    source_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CourseModule(BaseModel):
    number: int
    title: str
    outcome: str
    deliverable: str


class Offer(BaseModel):
    slug: str
    title: str
    subtitle: str
    audience: str
    promise: str
    outcomes: list[str]
    modules: list[CourseModule]
    price_jpy: int = Field(gt=0)
    regular_price_jpy: int = Field(gt=0)
    currency: str = "jpy"
    capacity: int = Field(gt=0)
    presale_opens: datetime
    presale_closes: datetime
    delivery_starts: datetime
    delivery_format: str
    support: str
    refund_policy: str
    checkout_label: str = "予約購入する"

    @property
    def presale_window_is_valid(self) -> bool:
        return self.presale_opens < self.presale_closes < self.delivery_starts
