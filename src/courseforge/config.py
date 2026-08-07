from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from courseforge.models import Offer


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    automation_enabled: bool = False
    publish_mode: Literal["draft", "live"] = "draft"
    approved_release_id: str = ""
    fulfillment_mode: Literal["draft", "live"] = "draft"

    state_db_path: Path = Path("var/courseforge.db")
    artifact_dir: Path = Path("artifacts")
    source_config_path: Path = Path("config/sources.yaml")
    model_config_path: Path = Path("config/models.yaml")
    offer_config_path: Path = Path("config/offer.yaml")
    legal_config_path: Path = Path("config/legal.yaml")

    notion_token: str | None = None
    notion_data_source_id: str | None = None
    notion_status_property: str = "Status"
    notion_allowed_statuses: str = "Approved,Reference"
    notion_confidentiality_property: str = "Confidentiality"
    notion_allowed_confidentialities: str = "Public"
    notion_allowed_use_property: str = "Allowed Use"
    notion_allowed_uses: str = "Summarize,Publish"
    notion_version: str = "2026-03-11"

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    codex_model: str = "gpt-5-codex"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-6"
    gemini_api_key: str | None = None
    antigravity_model: str = "antigravity-preview-05-2026"
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    max_paid_calls_per_run: int = 4
    daily_paid_token_budget: int = 120_000
    monthly_paid_token_budget: int = 1_500_000
    min_review_agents: int = 2

    stripe_secret_key: str | None = None
    stripe_payment_link_url: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_webhook_tolerance_seconds: int = 300
    checkout_success_url: str = "http://localhost:8000/thanks"
    shopify_store_domain: str | None = None
    shopify_admin_token: str | None = None
    shopify_api_version: str = "2026-07"
    shopify_publication_id: str | None = None

    resend_api_key: str | None = None
    from_email: str | None = None
    course_portal_url: str | None = None
    max_fulfillments_per_run: int = 50

    legal_seller_name: str | None = None
    legal_representative: str | None = None
    legal_address: str | None = None
    legal_phone: str | None = None
    legal_email: str | None = None
    legal_payment_timing: str | None = None
    legal_delivery_timing: str | None = None
    legal_refund_policy: str | None = None
    legal_additional_fees: str | None = None

    def secret_for_env(self, name: str | None) -> str | None:
        if not name:
            return None
        return os.getenv(name) or getattr(self, name.lower(), None)

    def value_for_env(self, name: str | None, default: str) -> str:
        if not name:
            return default
        value = os.getenv(name) or getattr(self, name.lower(), None)
        return str(value if value is not None else default)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return payload


def load_offer(path: Path) -> Offer:
    return Offer.model_validate(load_yaml(path))


def load_legal_profile(settings: Settings) -> dict[str, str]:
    file_values = load_yaml(settings.legal_config_path) if settings.legal_config_path.exists() else {}
    env_values = {
        "seller_name": settings.legal_seller_name,
        "representative": settings.legal_representative,
        "address": settings.legal_address,
        "phone": settings.legal_phone,
        "email": settings.legal_email,
        "payment_timing": settings.legal_payment_timing,
        "delivery_timing": settings.legal_delivery_timing,
        "refund_policy": settings.legal_refund_policy,
        "additional_fees": settings.legal_additional_fees,
    }
    return {key: str(env_values[key] or file_values.get(key, "")).strip() for key in env_values}


def legal_profile_complete(profile: dict[str, str]) -> bool:
    required = {
        "seller_name",
        "representative",
        "address",
        "phone",
        "email",
        "payment_timing",
        "delivery_timing",
        "refund_policy",
        "additional_fees",
    }
    invalid_markers = ("TODO", "DRAFT", "要入力", "未設定", "PLACEHOLDER")
    for key in required:
        value = profile.get(key, "").strip()
        if not value or any(marker.casefold() in value.casefold() for marker in invalid_markers):
            return False
    return True
