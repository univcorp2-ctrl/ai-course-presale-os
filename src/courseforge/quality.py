from __future__ import annotations

import json
import re
from typing import Any

from courseforge.llm import CompletionRequest, ModelRouter, RouterError
from courseforge.models import (
    AgentReview,
    ContentDraft,
    GateResult,
    Offer,
    ReviewFinding,
    Severity,
    SourceDocument,
    TaskKind,
)


FORBIDDEN_CLAIMS = {
    "guaranteed_income": re.compile(r"必ず.{0,12}(稼|儲)|誰でも.{0,12}(稼|儲)|収益保証"),
    "absolute_outcome": re.compile(r"100\s*%|絶対に成功|完全放置で"),
    "fabricated_social_proof": re.compile(r"受講者満足度\s*\d|累計受講者\s*\d|売上\s*\d+[万億]"),
}


def _shingles(text: str, size: int = 24) -> set[str]:
    normalized = re.sub(r"\s+", "", text).casefold()
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def deterministic_review(draft: ContentDraft, sources: list[SourceDocument], offer: Offer) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    body = draft.body_markdown
    for code, pattern in FORBIDDEN_CLAIMS.items():
        match = pattern.search(body)
        if match:
            findings.append(
                ReviewFinding(
                    code=code,
                    severity=Severity.HIGH,
                    message="誇大・未検証・誤認を招く可能性がある表現を検出しました。",
                    suggestion="測定可能な条件と根拠を示す表現へ置き換えてください。",
                    evidence=match.group(0),
                )
            )

    missing = [term for term in ["予約販売", "提供開始", "返金"] if term not in body]
    if missing:
        findings.append(
            ReviewFinding(
                code="missing_presale_disclosure",
                severity=Severity.HIGH,
                message=f"予約販売の重要表示が不足しています: {', '.join(missing)}",
                suggestion="提供開始日、価格、返金条件、提供内容を明示してください。",
            )
        )

    cited_labels = set(re.findall(r"\[(S\d+)\]", body))
    minimum_citations = min(2, len(sources))
    if len(cited_labels) < minimum_citations:
        findings.append(
            ReviewFinding(
                code="insufficient_citations",
                severity=Severity.HIGH,
                message="事実記述に対する出典ラベルが不足しています。",
                suggestion="少なくとも独立した2情報源を使い、[S1]形式で紐付けてください。",
            )
        )

    if len(body) < 1000:
        findings.append(
            ReviewFinding(
                code="thin_content",
                severity=Severity.MEDIUM,
                message="有料価値を検証するには本文が短すぎます。",
                suggestion="実例、演習、成果物、失敗時の対処を追加してください。",
            )
        )

    draft_shingles = _shingles(body)
    for source in sources:
        source_shingles = _shingles(source.text)
        if not draft_shingles or not source_shingles:
            continue
        overlap = len(draft_shingles & source_shingles) / max(1, min(len(draft_shingles), 2000))
        if overlap > 0.22:
            findings.append(
                ReviewFinding(
                    code="high_source_overlap",
                    severity=Severity.HIGH,
                    message=f"情報源「{source.title}」との文字列重複が高すぎます。",
                    suggestion="引用範囲を短くし、独自の分析・演習・意思決定基準へ再構成してください。",
                    evidence=f"overlap={overlap:.2%}",
                )
            )

    if not offer.presale_window_is_valid:
        findings.append(
            ReviewFinding(
                code="invalid_presale_dates",
                severity=Severity.CRITICAL,
                message="予約受付終了と提供開始日の順序が不正です。",
            )
        )
    return findings


def _extract_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def _score(value: object) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def parse_agent_review(*, provider_id: str, model: str, task: TaskKind, text: str, review_eligible: bool) -> AgentReview:
    payload = _extract_json(text)
    if not payload:
        return AgentReview(
            provider_id=provider_id,
            model=model,
            task=task,
            approved=False,
            score=0,
            review_eligible=review_eligible,
            findings=[ReviewFinding(
                code="unparseable_review",
                severity=Severity.HIGH,
                message="レビュー結果が指定JSON形式ではありません。",
                suggestion="同じ内容を構造化して再レビューしてください。",
            )],
            raw_excerpt=text[:1000],
        )
    findings: list[ReviewFinding] = []
    for item in payload.get("findings", []):
        try:
            findings.append(ReviewFinding.model_validate(item))
        except Exception:
            findings.append(ReviewFinding(code="invalid_review_finding", severity=Severity.MEDIUM, message=str(item)[:300]))
    return AgentReview(
        provider_id=provider_id,
        model=model,
        task=task,
        approved=bool(payload.get("approved", False)),
        score=_score(payload.get("score", 0)),
        findings=findings,
        review_eligible=review_eligible,
        raw_excerpt=text[:1000],
    )


class MultiAgentReviewer:
    def __init__(self, router: ModelRouter, min_agents: int) -> None:
        self.router = router
        self.min_agents = min_agents

    def review(self, draft: ContentDraft) -> list[AgentReview]:
        tasks = [TaskKind.FACT_CHECK, TaskKind.PEDAGOGY, TaskKind.COMPLIANCE]
        if "```" in draft.body_markdown:
            tasks.insert(0, TaskKind.CODE_EXAMPLE)
        reviews: list[AgentReview] = []
        used: set[str] = set()
        system_prompt = (
            "あなたは独立した品質審査担当です。本文を書き直さず、事実性、教育価値、"
            "誤認表示、知的財産、予約販売の透明性を厳格に審査してください。"
            "出力は approved, score, findings(code,severity,message,suggestion,evidence) を持つJSONだけにしてください。"
        )
        for task in tasks:
            request = CompletionRequest(
                task=task,
                system_prompt=system_prompt,
                user_prompt=f"審査観点: {task.value}\n\n本文:\n{draft.body_markdown[:18000]}",
                max_output_tokens=1800,
            )
            try:
                result = self.router.complete(
                    request,
                    exclude=used if len(used) < self.min_agents else set(),
                    review_only=True,
                )
            except RouterError:
                continue
            reviews.append(
                parse_agent_review(
                    provider_id=result.provider_id,
                    model=result.model,
                    task=task,
                    text=result.text,
                    review_eligible=result.review_eligible,
                )
            )
            used.add(result.provider_id)
        return reviews


class ReviewGate:
    @staticmethod
    def evaluate(*, agent_reviews: list[AgentReview], deterministic_findings: list[ReviewFinding], min_agents: int, for_live: bool, legal_complete: bool) -> GateResult:
        eligible_reviews = [review for review in agent_reviews if review.review_eligible]
        provider_count = len({review.provider_id for review in eligible_reviews})
        blockers = [finding for finding in deterministic_findings if finding.severity in {Severity.HIGH, Severity.CRITICAL}]
        warnings = [finding for finding in deterministic_findings if finding.severity not in {Severity.HIGH, Severity.CRITICAL}]
        if provider_count < min_agents:
            blockers.append(
                ReviewFinding(
                    code="insufficient_independent_reviewers",
                    severity=Severity.HIGH,
                    message=f"独立レビュー担当が{provider_count}件しかありません。最低{min_agents}件必要です。",
                )
            )
        for review in eligible_reviews:
            blockers.extend(finding for finding in review.findings if finding.severity in {Severity.HIGH, Severity.CRITICAL})
            warnings.extend(finding for finding in review.findings if finding.severity not in {Severity.HIGH, Severity.CRITICAL})
            if not review.approved:
                blockers.append(
                    ReviewFinding(
                        code=f"agent_rejected_{review.task.value}",
                        severity=Severity.HIGH,
                        message=f"{review.provider_id} の {review.task.value} 審査が承認されませんでした。",
                    )
                )
        if for_live and not legal_complete:
            blockers.append(
                ReviewFinding(
                    code="legal_profile_incomplete",
                    severity=Severity.CRITICAL,
                    message="特定商取引法表示に必要な販売者情報が未完成です。",
                )
            )
        return GateResult(
            passed=not blockers,
            blockers=blockers,
            warnings=warnings,
            distinct_review_providers=provider_count,
        )
