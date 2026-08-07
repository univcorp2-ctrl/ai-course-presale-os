from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from courseforge.config import Settings, load_yaml
from courseforge.models import TaskKind
from courseforge.state import StateStore


class ProviderSpec(BaseModel):
    id: str
    kind: Literal["heuristic", "ollama", "openai", "anthropic", "antigravity"]
    default_model: str
    model_env: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    enabled_env: str | None = None
    paid: bool = False
    review_eligible: bool = True
    daily_token_limit: int = 250_000
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    tasks: list[TaskKind] = Field(default_factory=list)


class ModelPolicy(BaseModel):
    providers: list[ProviderSpec]
    routes: dict[str, list[str]]

    @classmethod
    def from_path(cls, path: Path) -> "ModelPolicy":
        return cls.model_validate(load_yaml(path))

    def route_for(self, task: TaskKind) -> list[str]:
        return self.routes.get(task.value, [])


class CompletionRequest(BaseModel):
    task: TaskKind
    system_prompt: str
    user_prompt: str
    max_output_tokens: int = 3000


class CompletionResult(BaseModel):
    provider_id: str
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    review_eligible: bool


class RouterError(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


class BaseProvider:
    def __init__(self, spec: ProviderSpec, model: str) -> None:
        self.spec = spec
        self.model = model

    @property
    def available(self) -> bool:
        return True

    def complete(self, request: CompletionRequest) -> CompletionResult:
        raise NotImplementedError

    def _result(self, request: CompletionRequest, text: str, input_tokens: int | None = None, output_tokens: int | None = None) -> CompletionResult:
        input_count = input_tokens or estimate_tokens(request.system_prompt + request.user_prompt)
        output_count = output_tokens or estimate_tokens(text)
        cost = (
            input_count * self.spec.input_cost_per_million
            + output_count * self.spec.output_cost_per_million
        ) / 1_000_000
        return CompletionResult(
            provider_id=self.spec.id,
            model=self.model,
            text=text,
            input_tokens=input_count,
            output_tokens=output_count,
            estimated_cost_usd=round(cost, 6),
            review_eligible=self.spec.review_eligible,
        )


class HeuristicProvider(BaseProvider):
    def complete(self, request: CompletionRequest) -> CompletionResult:
        if request.task in {TaskKind.FACT_CHECK, TaskKind.PEDAGOGY, TaskKind.COMPLIANCE, TaskKind.CODE_EXAMPLE}:
            text = json.dumps(
                {
                    "approved": False,
                    "score": 45,
                    "findings": [{
                        "code": "external_review_required",
                        "severity": "high",
                        "message": "独立したレビュー対象モデルを利用できませんでした。",
                        "suggestion": "最低2つの独立プロバイダーを設定してください。",
                    }],
                },
                ensure_ascii=False,
            )
        elif request.task == TaskKind.TRIAGE:
            text = "実務成果、再現可能な手順、更新性、根拠の明示を優先する。"
        else:
            text = (
                "## 講座の目的\n"
                "受講者がAI導入を小さく検証し、測定可能な業務改善として定着させる。 [S1]\n\n"
                "## 学習ステップ\n"
                "1. 課題と成功指標を定義する。\n"
                "2. 情報源と権限を整理する。\n"
                "3. 小さな自動化を実装し、品質と費用を計測する。\n"
                "4. レビューと改善の仕組みを運用する。\n\n"
                "## 実践課題\n"
                "自社の1業務を選び、入力・処理・出力・例外・責任者を1枚にまとめる。"
            )
        return self._result(request, text)


class OpenAIProvider(BaseProvider):
    def __init__(self, spec: ProviderSpec, model: str, api_key: str | None) -> None:
        super().__init__(spec, model)
        self.api_key = api_key
        self.base_url = spec.base_url or "https://api.openai.com/v1"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        response = httpx.post(
            f"{self.base_url}/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "input": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "max_output_tokens": request.max_output_tokens,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
        text = str(data.get("output_text", ""))
        if not text:
            parts: list[str] = []
            for item in data.get("output", []):
                for content in item.get("content", []):
                    value = content.get("text")
                    if isinstance(value, str):
                        parts.append(value)
            text = "\n".join(parts)
        usage = data.get("usage", {})
        return self._result(
            request,
            text,
            int(usage.get("input_tokens", 0)) or None,
            int(usage.get("output_tokens", 0)) or None,
        )


class AnthropicProvider(BaseProvider):
    def __init__(self, spec: ProviderSpec, model: str, api_key: str | None) -> None:
        super().__init__(spec, model)
        self.api_key = api_key
        self.base_url = spec.base_url or "https://api.anthropic.com/v1"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        response = httpx.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": str(self.api_key),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "system": request.system_prompt,
                "messages": [{"role": "user", "content": request.user_prompt}],
                "max_tokens": request.max_output_tokens,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
        text = "\n".join(
            str(item.get("text", ""))
            for item in data.get("content", [])
            if item.get("type") == "text"
        )
        usage = data.get("usage", {})
        return self._result(
            request,
            text,
            int(usage.get("input_tokens", 0)) or None,
            int(usage.get("output_tokens", 0)) or None,
        )


class OllamaProvider(BaseProvider):
    def __init__(self, spec: ProviderSpec, model: str, base_url: str, enabled: bool) -> None:
        super().__init__(spec, model)
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled

    @property
    def available(self) -> bool:
        return self.enabled

    def complete(self, request: CompletionRequest) -> CompletionResult:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
            },
            timeout=240.0,
        )
        response.raise_for_status()
        data = response.json()
        return self._result(
            request,
            str(data.get("message", {}).get("content", "")),
            int(data.get("prompt_eval_count", 0)) or None,
            int(data.get("eval_count", 0)) or None,
        )


class AntigravityProvider(BaseProvider):
    def __init__(self, spec: ProviderSpec, model: str, api_key: str | None) -> None:
        super().__init__(spec, model)
        self.api_key = api_key
        self.base_url = spec.base_url or "https://generativelanguage.googleapis.com/v1beta"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        prompt = f"SYSTEM:\n{request.system_prompt}\n\nTASK:\n{request.user_prompt}"
        response = httpx.post(
            f"{self.base_url}/interactions",
            headers={"x-goog-api-key": str(self.api_key), "content-type": "application/json"},
            json={
                "agent": self.model,
                "input": prompt,
                "environment": "remote",
                "tools": [{"type": "google_search"}, {"type": "url_context"}],
                "agent_config": {"type": "antigravity", "model": "gemini-3.5-flash-lite"},
            },
            timeout=300.0,
        )
        response.raise_for_status()
        data = response.json()
        text = str(data.get("output_text", ""))
        if not text:
            raise RuntimeError("Antigravity interaction did not return output_text")
        return self._result(request, text)


class ProviderFactory:
    @staticmethod
    def create(spec: ProviderSpec, settings: Settings) -> BaseProvider:
        model = settings.value_for_env(spec.model_env, spec.default_model)
        if spec.kind == "heuristic":
            return HeuristicProvider(spec, model)
        if spec.kind == "ollama":
            enabled = settings.ollama_enabled
            if spec.enabled_env:
                enabled = settings.value_for_env(spec.enabled_env, str(enabled)).casefold() in {"1", "true", "yes", "on"}
            return OllamaProvider(spec, model, settings.ollama_base_url, enabled)
        api_key = settings.secret_for_env(spec.api_key_env)
        if spec.kind == "openai":
            return OpenAIProvider(spec, model, api_key)
        if spec.kind == "anthropic":
            return AnthropicProvider(spec, model, api_key)
        if spec.kind == "antigravity":
            return AntigravityProvider(spec, model, api_key)
        raise ValueError(f"Unsupported provider kind: {spec.kind}")


class ModelRouter:
    def __init__(self, *, policy: ModelPolicy, providers: dict[str, BaseProvider], state: StateStore, settings: Settings) -> None:
        self.policy = policy
        self.providers = providers
        self.state = state
        self.settings = settings
        self.paid_calls_this_run = 0

    @staticmethod
    def _day_start() -> datetime:
        now = datetime.now(timezone.utc)
        return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)

    @staticmethod
    def _month_start() -> datetime:
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    def _budget_allows(self, provider: BaseProvider, estimated_tokens: int) -> bool:
        spec = provider.spec
        if not spec.paid:
            return True
        if self.paid_calls_this_run >= self.settings.max_paid_calls_per_run:
            return False
        daily_used = self.state.paid_tokens_since(self._day_start())
        monthly_used = self.state.paid_tokens_since(self._month_start())
        provider_used = self.state.provider_tokens_since(spec.id, self._day_start())
        return (
            daily_used + estimated_tokens <= self.settings.daily_paid_token_budget
            and monthly_used + estimated_tokens <= self.settings.monthly_paid_token_budget
            and provider_used + estimated_tokens <= spec.daily_token_limit
        )

    def complete(self, request: CompletionRequest, *, exclude: set[str] | None = None, review_only: bool = False) -> CompletionResult:
        excluded = exclude or set()
        estimated_tokens = estimate_tokens(request.system_prompt + request.user_prompt) + request.max_output_tokens
        errors: list[str] = []
        for provider_id in self.policy.route_for(request.task):
            if provider_id in excluded:
                continue
            provider = self.providers.get(provider_id)
            if not provider or request.task not in provider.spec.tasks:
                continue
            if review_only and not provider.spec.review_eligible:
                continue
            if not provider.available or not self._budget_allows(provider, estimated_tokens):
                continue
            if provider.spec.paid:
                self.paid_calls_this_run += 1
            try:
                result = provider.complete(request)
            except Exception as exc:
                errors.append(f"{provider_id}: {type(exc).__name__}: {exc}")
                continue
            self.state.record_usage(
                provider_id=result.provider_id,
                model=result.model,
                task=request.task.value,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                estimated_cost_usd=result.estimated_cost_usd,
                paid=provider.spec.paid,
            )
            return result
        detail = "; ".join(errors) if errors else "no available provider within policy and budget"
        raise RouterError(f"No provider completed {request.task.value}: {detail}")


def build_router(settings: Settings, state: StateStore) -> ModelRouter:
    policy = ModelPolicy.from_path(settings.model_config_path)
    providers = {spec.id: ProviderFactory.create(spec, settings) for spec in policy.providers}
    return ModelRouter(policy=policy, providers=providers, state=state, settings=settings)
