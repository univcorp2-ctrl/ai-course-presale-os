from __future__ import annotations

import csv
import json
from pathlib import Path

from courseforge.models import ContentDraft, Offer, ReleaseManifest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, default=str))


def export_platform_packages(
    *,
    draft: ContentDraft,
    offer: Offer,
    manifest: ReleaseManifest,
    release_dir: Path,
) -> None:
    source_lines = "\n".join(
        f"- [{label}] {url}" for label, url in draft.source_labels.items() if url
    )
    direct = f"""# {offer.title}

{offer.subtitle}

**対象:** {offer.audience}

**予約価格:** ¥{offer.price_jpy:,}（通常予定価格 ¥{offer.regular_price_jpy:,}）

**予約受付:** {offer.presale_opens.isoformat()} 〜 {offer.presale_closes.isoformat()}

**提供開始:** {offer.delivery_starts.isoformat()}

{draft.body_markdown}

## 出典
{source_lines or '- ローカルの企画資料のみ'}
"""
    _write(release_dir / "packages/direct/landing.md", direct)

    preview = draft.body_markdown[:2500]
    note_package = f"""# {offer.title}を作る理由と、受講後に残る成果物

> この記事は予約販売中の講座の無料プレビューです。AIを制作補助に利用し、人間と複数モデルの審査を通してから公開します。

{preview}

## 予約販売のご案内
- 提供開始: {offer.delivery_starts.isoformat()}
- 予約価格: ¥{offer.price_jpy:,}
- 返金条件: {offer.refund_policy}
- 購入先: {{CHECKOUT_URL}}

※ noteには一般公開の投稿APIがないため、このファイルを人が最終確認して入稿します。
"""
    _write(release_dir / "packages/note/article.md", note_package)

    udemy_dir = release_dir / "packages/udemy"
    udemy_dir.mkdir(parents=True, exist_ok=True)
    _write(
        udemy_dir / "course-landing-page.md",
        f"""# {offer.title}

## 一文で伝える価値
{offer.promise}

## 対象受講者
{offer.audience}

## 学習成果
{chr(10).join(f'- {item}' for item in offer.outcomes)}

## 前提条件
- 自分の業務課題を1つ持っていること
- 各AIサービスの利用規約と社内ルールを確認できること

## 品質方針
実在しない実績や収益保証は使わず、演習・テンプレート・更新履歴で価値を示します。
""",
    )
    with (udemy_dir / "curriculum.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["section", "title", "outcome", "deliverable"])
        for module in offer.modules:
            writer.writerow([module.number, module.title, module.outcome, module.deliverable])

    brain = f"""# {offer.title}

## こんな悩みを解決します
{offer.subtitle}

## 購入後に手元へ残るもの
{chr(10).join(f'- {module.deliverable}' for module in offer.modules)}

## 提供条件
- 予約価格: ¥{offer.price_jpy:,}
- 提供開始: {offer.delivery_starts.isoformat()}
- サポート: {offer.support}
- 返金条件: {offer.refund_policy}

## 販売倫理
紹介報酬を設定する場合も、虚偽の体験談、収益保証、煽り目的の架空在庫は使用しません。

※ Brainの公開APIを前提にせず、最終入稿はプラットフォーム画面で行います。
"""
    _write(release_dir / "packages/brain/sales-page.md", brain)

    email = f"""件名: AIを『使う』から、改善できる業務フローへ

{offer.audience}向けに、{offer.title}の予約受付を準備しています。
この講座で作るのはプロンプト集だけではありません。業務選定、情報源、費用上限、レビュー、例外処理まで含む運用設計です。

提供開始: {offer.delivery_starts.date().isoformat()}
予約価格: ¥{offer.price_jpy:,}
詳細: {{LANDING_URL}}

配信停止: {{UNSUBSCRIBE_URL}}
"""
    _write(release_dir / "packages/marketing/email-01.txt", email)
    _write(
        release_dir / "packages/marketing/social-posts.md",
        f"""# 投稿案

## X / Threads
AI導入で最初に作るべきものは、万能プロンプトではなく「失敗しても止められる小さな業務フロー」。入力、根拠、費用上限、レビュー、例外処理まで実装する講座を準備中です。提供開始は{offer.delivery_starts.date().isoformat()}。{{LANDING_URL}}

## LinkedIn
生成AI研修を、機能紹介ではなく業務成果へつなげる実践講座を設計しました。受講後には、業務選定シート、情報源台帳、モデル振り分け表、品質ゲート、運用ダッシュボードが残ります。{{LANDING_URL}}
""",
    )

    checklist = """# 公開前チェックリスト

- [ ] 本文中の事実と出典を人が照合した
- [ ] 複数の独立モデルによるレビューが承認済み
- [ ] 実在しない実績、推薦、レビュー、限定数を使っていない
- [ ] 予約価格、受付期間、提供開始日、返金条件が一致している
- [ ] 特定商取引法表示と最終確認画面を確認した
- [ ] メール送信対象は事前同意済みで、配信停止手段がある
- [ ] 各プラットフォームの最新規約を公開日に再確認した
"""
    _write(release_dir / "packages/MANUAL_PUBLISH_CHECKLIST.md", checklist)
    _write_json(release_dir / "manifest.json", manifest.model_dump(mode="json"))
    _write_json(release_dir / "draft.json", draft.model_dump(mode="json"))
