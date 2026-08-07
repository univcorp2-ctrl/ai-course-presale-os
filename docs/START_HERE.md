# CourseForgeを始める

このリポジトリは、AI講座の調査、生成、複数審査、予約販売、購入後案内、配信パッケージ化を一つにまとめた運用基盤です。初期商品は「AI業務フロー構築 実践ブートキャンプ」です。

## 1. セットアップ

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
courseforge doctor
```

Ollamaを使う場合はモデルを取得してから有効化します。

```bash
docker compose --profile local-model up -d ollama
# ホストまたはコンテナのOllamaへ qwen3:8b 等を準備
# .env: OLLAMA_ENABLED=true
```

## 2. Notionを接続

Notionで内部インテグレーションを作り、対象データソースを接続先へ共有します。現行APIではデータベースの配下にある data source ID を使います。

```dotenv
NOTION_TOKEN=...
NOTION_DATA_SOURCE_ID=...
```

`Status=Approved/Reference`、`Confidentiality=Public`、`Allowed Use=Summarize/Publish` のページだけが既定で外部モデルへ渡ります。推奨プロパティは `docs/NOTION_SCHEMA.md` にあります。

## 3. 下書きを生成

```bash
courseforge run-daily
```

成果物は `artifacts/releases/<release-id>/` に保存されます。

- `draft.json`: 生成本文とモデル履歴
- `sources.json`: 使用した情報源と取得時刻
- `manifest.json`: 決定論チェック、各エージェント審査、承認状態
- `packages/`: 直販、note、Udemy、Brain、メール、SNS用の入稿物

少なくとも2つの独立した審査プロバイダーが承認し、高・重大指摘がなくなるまで `gate_passed` は真になりません。

## 4. 人が承認

```bash
courseforge approve <release-id> --reviewer "担当者名"
```

承認は品質ゲート通過後だけ可能です。外部販売には、さらに `PUBLISH_MODE=live`、一致する `APPROVED_RELEASE_ID`、完成済み特商法表示が必要です。

## 5. 販売計画を確認

```bash
courseforge publish <release-id> --channels stripe,shopify
```

これはAPIを呼ばない計画表示です。ライブ実行は次の条件をすべて満たした場合だけです。

```bash
courseforge publish <release-id> --channels stripe --live
```

`config/offer.yaml` の返金条件には初期状態で `DRAFT` があり、そのままではライブ販売できません。販売主体の情報と法務確認を完了してから更新してください。

## 6. 購入後案内

StripeのWebhook送信先を `/webhooks/stripe` に設定します。署名検証に成功した `checkout.session.completed` だけが受講者キューへ入ります。

```bash
courseforge fulfill-pending          # 送信計画だけ表示
courseforge fulfill-pending --live   # FULFILLMENT_MODE=live のときだけ送信
```

Webhook処理はメールを直接送らないため、決済通知の再送やメールAPI障害でも購入記録を失いません。

## 7. ランディングページ

```bash
courseforge serve --host 0.0.0.0 --port 8000
```

`STRIPE_PAYMENT_LINK_URL` が空の間、購入ボタンは無効です。本番公開は初期構築では実施していません。
