from courseforge.models import AgentReview, ReviewFinding, Severity, TaskKind
from courseforge.quality import ReviewGate


def _approved(provider: str, task: TaskKind) -> AgentReview:
    return AgentReview(
        provider_id=provider,
        model="test",
        task=task,
        approved=True,
        score=90,
        review_eligible=True,
    )


def test_review_gate_requires_two_distinct_providers() -> None:
    result = ReviewGate.evaluate(
        agent_reviews=[
            _approved("provider-a", TaskKind.FACT_CHECK),
            _approved("provider-a", TaskKind.PEDAGOGY),
        ],
        deterministic_findings=[],
        min_agents=2,
        for_live=False,
        legal_complete=False,
    )
    assert not result.passed
    assert any(item.code == "insufficient_independent_reviewers" for item in result.blockers)


def test_review_gate_blocks_high_severity_findings() -> None:
    result = ReviewGate.evaluate(
        agent_reviews=[
            _approved("provider-a", TaskKind.FACT_CHECK),
            _approved("provider-b", TaskKind.PEDAGOGY),
        ],
        deterministic_findings=[
            ReviewFinding(code="bad_claim", severity=Severity.HIGH, message="unsupported")
        ],
        min_agents=2,
        for_live=False,
        legal_complete=False,
    )
    assert not result.passed
