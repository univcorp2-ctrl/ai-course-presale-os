from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from courseforge.config import Settings, legal_profile_complete, load_legal_profile, load_offer
from courseforge.exporters import export_platform_packages
from courseforge.llm import CompletionRequest, ModelRouter, RouterError, build_router
from courseforge.models import ContentDraft, Offer, ReleaseManifest, SourceDocument, TaskKind
from courseforge.quality import MultiAgentReviewer, ReviewGate, deterministic_review
from courseforge.sources import collect_sources
from courseforge.state import StateStore


class CourseForgePipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.state = StateStore(self.settings.state_db_path)
        self.router: ModelRouter = build_router(self.settings, self.state)

    @staticmethod
    def _release_id(slug: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{slug}-{timestamp}"

    @staticmethod
    def _source_context(documents: list[SourceDocument]) -> tuple[str, dict[str, str]]:
        chunks: list[str] = []
        labels: dict[str, str] = {}
        for index, document in enumerate(documents[:12], start=1):
            label = f"S{index}"
            labels[label] = str(document.url or document.metadata.get("path", document.id))
            chunks.append(
                f"[{label}] {document.title}\nsource_type={document.source_type}\n"
                f"url={document.url or 'local'}\n{document.text[:1800].strip()}"
            )
        return "\n\n---\n\n".join(chunks), labels

    def _generate(self, *, offer: Offer, documents: list[SourceDocument], release_id: str) -> ContentDraft:
        source_context, labels = self._source_context(documents)
        trace: list[dict[str, object]] = []
        try:
            triage = self.router.complete(
                CompletionRequest(
                    task=TaskKind.TRIAGE,
                    system_prompt=(
                        "情報源を、実務価値、鮮度、根拠の強さ、講座演習への変換可能性で順位付けする。"
                        "本文のコピーはせず、優先テーマだけ返す。"
                    ),
                    user_prompt=source_context[:10000],
                    max_output_tokens=800,
                )
            )
            triage_text = triage.text
            trace.append(triage.model_dump(mode="json"))
        except RouterError as exc:
            triage_text = f"Triage unavailable: {exc}"

        system_prompt = """あなたは日本語の実務教育コンテンツ設計者です。
情報源を要約して並べるのではなく、受講者が仕事で使える判断基準、手順、演習、失敗時の対処へ変換してください。
事実には必ず [S1] のような出典ラベルを付け、引用は最小限にし、独自の統合・比較・テンプレートを中心にします。
収益保証、架空の実績、偽のレビュー、過度な希少性は使いません。
見出し、演習、成果物、チェックリストを含む完成度の高いMarkdownを返してください。"""
        user_prompt = f"""講座: {offer.title}
対象: {offer.audience}
約束する成果: {offer.promise}
学習成果: {json.dumps(offer.outcomes, ensure_ascii=False)}
優先テーマ: {triage_text[:3000]}

情報源:
{source_context}

構成要件:
1. なぜ今この課題か
2. 成果が出る人・出にくい人
3. 具体的な実装手順
4. ケーススタディ
5. 受講者が作る成果物
6. 品質・費用・権限・法令のチェック
7. 次の行動
"""
        try:
            synthesis = self.router.complete(
                CompletionRequest(
                    task=TaskKind.SYNTHESIS,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=6500,
                )
            )
            body = synthesis.text.strip()
            trace.append(synthesis.model_dump(mode="json"))
        except RouterError as exc:
            body = (
                "## 生成モデルが利用できないための安全な下書き\n\n"
                f"{exc}\n\n"
                "## 実務設計の基本\n"
                "対象業務、成功指標、入力情報、出力、例外、責任者を定義します。 [S1]\n\n"
                "## 演習\n自分の業務を1つ選び、現状時間と失敗コストを記録してください。"
            )

        disclosure = f"""

---

## 予約販売について

本講座は予約販売です。予約受付は **{offer.presale_opens.isoformat()}** から
**{offer.presale_closes.isoformat()}** まで、提供開始は **{offer.delivery_starts.isoformat()}** です。
予約価格は **¥{offer.price_jpy:,}**、通常予定価格は **¥{offer.regular_price_jpy:,}** です。
返金条件は「{offer.refund_policy}」です。提供内容・日程に変更が生じる場合は購入者へ通知します。

## AI利用と品質管理

AIは情報整理と草案作成に利用します。公開前に出典照合、重複検査、複数の独立モデルによる
事実・教育・表示審査を行い、未承認の内容は販売・配信しません。
"""
        return ContentDraft(
            release_id=release_id,
            slug=offer.slug,
            title=offer.title,
            audience=offer.audience,
            body_markdown=f"# {offer.title}\n\n{offer.subtitle}\n\n{body}{disclosure}",
            source_labels=labels,
            model_trace=trace,
        )

    def run_daily(self) -> tuple[ReleaseManifest, Path]:
        offer = load_offer(self.settings.offer_config_path)
        documents, source_warnings = collect_sources(self.settings)
        if not documents:
            raise RuntimeError("No usable source documents were collected")
        release_id = self._release_id(offer.slug)
        draft = self._generate(offer=offer, documents=documents, release_id=release_id)
        deterministic = deterministic_review(draft, documents, offer)
        agent_reviews = MultiAgentReviewer(self.router, self.settings.min_review_agents).review(draft)
        legal_profile = load_legal_profile(self.settings)
        legal_complete = legal_profile_complete(legal_profile)
        gate = ReviewGate.evaluate(
            agent_reviews=agent_reviews,
            deterministic_findings=deterministic,
            min_agents=self.settings.min_review_agents,
            for_live=False,
            legal_complete=legal_complete,
        )
        manifest = ReleaseManifest(
            release_id=release_id,
            slug=offer.slug,
            content_hash=draft.content_hash(),
            gate_passed=gate.passed,
            legal_complete=legal_complete,
            deterministic_findings=deterministic,
            agent_reviews=agent_reviews,
            source_count=len(documents),
            warnings=source_warnings + [f"gate: {item.code}: {item.message}" for item in gate.blockers],
        )
        release_dir = self.settings.artifact_dir / "releases" / release_id
        export_platform_packages(draft=draft, offer=offer, manifest=manifest, release_dir=release_dir)
        (release_dir / "sources.json").write_text(
            json.dumps([document.model_dump(mode="json") for document in documents], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self.state.save_manifest(manifest)
        return manifest, release_dir

    def approve(self, release_id: str, reviewer: str) -> ReleaseManifest:
        release_dir = self.settings.artifact_dir / "releases" / release_id
        manifest_path = release_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Release manifest not found: {manifest_path}")
        manifest = ReleaseManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if not manifest.gate_passed:
            raise RuntimeError("Quality gate has not passed; approval is blocked")
        if not reviewer.strip():
            raise ValueError("Reviewer name is required")
        manifest.status = "approved"
        manifest.approved_by = reviewer.strip()
        manifest.approved_at = datetime.now(timezone.utc)
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        self.state.save_manifest(manifest)
        return manifest

    def load_release(self, release_id: str) -> ReleaseManifest:
        path = self.settings.artifact_dir / "releases" / release_id / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Release manifest not found: {path}")
        return ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
