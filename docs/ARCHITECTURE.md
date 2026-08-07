# アーキテクチャ

## パイプライン

```text
Notion data source ─┐
Official RSS feeds ─┼─> normalize / rights filter / deduplicate / provenance
Local principles ───┘                         │
                                              v
                                 budget-aware model router
                    Ollama / Claude / OpenAI / Codex / Antigravity
                                              │
                                              v
                              original course draft + [S#] citations
                                              │
              deterministic checks ───────────┼──────── independent agents
              overlap / hype / dates          │         fact / pedagogy /
                                              │         compliance / code
                                              v
                                       review gate
                                              │
                         ┌────────────────────┴───────────────────┐
                         v                                        v
                 draft packages                         approved manifest
          note / Udemy / Brain / email             + exact content hash
                         │                                        │
                         └──────────────────┬─────────────────────┘
                                            v
                         Stripe / optional Shopify official APIs
                                            │
                                      signed webhook
                                            │
                                      enrollment queue
                                            │
                                    transactional email
```

## モデルの役割

- **Ollama**: トピック仕分け、要約、形式検査など、低リスク・大量処理。ローカル実行を優先。
- **Claude**: 長文の教材設計、教育的な再構成、編集レビュー。
- **OpenAI**: 一般的な統合、表示・コンプライアンス審査。モデル名は環境変数で交換可能。
- **Codex**: 教材中のコード例や自動化手順のレビューに限定。コードがない回は呼ばない。
- **Antigravity**: タイムリーな調査と独立ファクトチェック。プレビューAPIのため回数を低く制限。
- **deterministic fallback**: API障害時に草案だけ残す。審査者としては数えず、無人公開へ進めない。

## リソース制御

`config/models.yaml` にタスク別の優先順、プロバイダー別日次上限を設定します。全体では次を強制します。

- 1実行あたりの有料呼び出し上限
- 日次・月次の有料推定トークン上限
- プロバイダー別日次上限
- 失敗した同一プロバイダーを同じ要求で繰り返さず、次の経路へ一度だけフォールバック
- コードがない場合はCodexを呼ばないなど、必要性ベースの起動

利用量はSQLiteへ保存され、秘密値やプロンプト全文は記録しません。価格は変動するため、契約中の実単価を運用時に設定します。単価が未設定でもトークン上限は機能します。

## 公開境界

`run-daily` は下書きと入稿パッケージだけを作ります。ライブ販売には、品質ゲート、人の承認、内容ハッシュ、リリースID、法定表示、`PUBLISH_MODE=live` の全一致が必要です。note、Udemy、Brainは公式の一般向け出品APIを前提にせず、入稿物生成までに止めます。
