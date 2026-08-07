from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn

from courseforge.commerce import CommerceService
from courseforge.config import (
    Settings,
    legal_profile_complete,
    load_legal_profile,
    load_offer,
)
from courseforge.llm import ModelPolicy, ProviderFactory
from courseforge.pipeline import CourseForgePipeline

app = typer.Typer(no_args_is_help=True, help="AI講座の生成・審査・予約販売を運用します。")


@app.command()
def doctor() -> None:
    """秘密値を表示せず、運用準備状況を確認します。"""
    settings = Settings()
    policy = ModelPolicy.from_path(settings.model_config_path)
    providers = {
        spec.id: ProviderFactory.create(spec, settings) for spec in policy.providers
    }
    legal = load_legal_profile(settings)
    report = {
        "environment": settings.environment,
        "publish_mode": settings.publish_mode,
        "automation_enabled": settings.automation_enabled,
        "notion_ready": bool(settings.notion_token and settings.notion_data_source_id),
        "providers": {
            provider_id: {
                "available": provider.available,
                "paid": provider.spec.paid,
                "review_eligible": provider.spec.review_eligible,
                "model": provider.model,
            }
            for provider_id, provider in providers.items()
        },
        "legal_profile_complete": legal_profile_complete(legal),
        "stripe_ready": bool(settings.stripe_secret_key),
        "shopify_ready": bool(
            settings.shopify_store_domain
            and settings.shopify_admin_token
            and settings.shopify_publication_id
        ),
    }
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("run-daily")
def run_daily() -> None:
    """情報源を収集し、下書き・複数審査・配信パッケージを生成します。"""
    manifest, release_dir = CourseForgePipeline().run_daily()
    typer.echo(
        json.dumps(
            {
                "release_id": manifest.release_id,
                "gate_passed": manifest.gate_passed,
                "source_count": manifest.source_count,
                "artifact_dir": str(release_dir),
                "warnings": manifest.warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def approve(release_id: str, reviewer: str = typer.Option(..., "--reviewer")) -> None:
    """品質ゲート通過済みリリースを人が承認します。"""
    manifest = CourseForgePipeline().approve(release_id, reviewer)
    typer.echo(manifest.model_dump_json(indent=2))


@app.command()
def publish(
    release_id: str,
    channels: str = typer.Option("stripe", "--channels"),
    live: bool = typer.Option(False, "--live"),
) -> None:
    """販売チャネルの実行計画を表示し、--live時だけ外部へ反映します。"""
    settings = Settings()
    pipeline = CourseForgePipeline(settings)
    manifest = pipeline.load_release(release_id)
    offer = load_offer(settings.offer_config_path)
    legal = load_legal_profile(settings)
    selected = [item.strip() for item in channels.split(",") if item.strip()]
    results = CommerceService(settings).execute(
        channels=selected,
        live=live,
        release_id=release_id,
        manifest=manifest,
        offer=offer,
        legal_profile=legal,
    )
    release_dir = settings.artifact_dir / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "commerce-result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if live:
        (release_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        pipeline.state.save_manifest(manifest)
    typer.echo(json.dumps(results, ensure_ascii=False, indent=2, default=str))


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """予約販売ランディングページを起動します。"""
    uvicorn.run("courseforge.web:app", host=host, port=port, reload=reload)


@app.command("show-release")
def show_release(release_id: str) -> None:
    path = Path(Settings().artifact_dir) / "releases" / release_id / "manifest.json"
    if not path.exists():
        raise typer.BadParameter(f"Unknown release: {release_id}")
    typer.echo(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
