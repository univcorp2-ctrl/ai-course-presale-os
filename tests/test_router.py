from pathlib import Path

from courseforge.config import Settings
from courseforge.llm import (
    CompletionRequest,
    HeuristicProvider,
    ModelPolicy,
    ModelRouter,
    ProviderSpec,
)
from courseforge.models import TaskKind
from courseforge.state import StateStore


def test_router_uses_free_fallback_when_paid_budget_is_too_small(tmp_path: Path) -> None:
    paid_spec = ProviderSpec(
        id="paid",
        kind="heuristic",
        default_model="paid-test",
        paid=True,
        review_eligible=True,
        daily_token_limit=100000,
        tasks=[TaskKind.SYNTHESIS],
    )
    free_spec = ProviderSpec(
        id="free",
        kind="heuristic",
        default_model="free-test",
        paid=False,
        review_eligible=False,
        tasks=[TaskKind.SYNTHESIS],
    )
    policy = ModelPolicy(
        providers=[paid_spec, free_spec],
        routes={TaskKind.SYNTHESIS.value: ["paid", "free"]},
    )
    providers = {
        "paid": HeuristicProvider(paid_spec, "paid-test"),
        "free": HeuristicProvider(free_spec, "free-test"),
    }
    settings = Settings(
        state_db_path=tmp_path / "state.db",
        daily_paid_token_budget=10,
        monthly_paid_token_budget=10,
    )
    router = ModelRouter(
        policy=policy,
        providers=providers,
        state=StateStore(settings.state_db_path),
        settings=settings,
    )
    result = router.complete(
        CompletionRequest(
            task=TaskKind.SYNTHESIS,
            system_prompt="system",
            user_prompt="long enough to exceed ten estimated tokens",
            max_output_tokens=100,
        )
    )
    assert result.provider_id == "free"
