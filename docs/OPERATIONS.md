# 運用手順

## 日次

1. NotionのApproved/Referenceかつ公開利用可能な情報と、許可済みRSSを取得。
2. 重複を除き、出典、取得時刻、URL、利用条件を保存。
3. Ollama等で仕分けし、高品質モデルで教材へ再構成。
4. 決定論チェックと独立エージェント審査。
5. `gate_passed=false` でも下書きは保存し、外部公開は止める。
6. note/Udemy/Brain/メール/SNSの入稿パッケージを生成。

GitHub ActionsのcronはRepository Variable `AUTOMATION_ENABLED=true` のときだけ実行されます。初期値は停止です。

## 週次編集会議

- 高・重大指摘を解消する。
- ニュース由来の事実を一次情報で再確認する。
- 受講者の質問を演習やFAQへ反映する。
- 古くなった手順へ検証期限を付ける。
- 本文と出典の内容ハッシュを固定し、人が承認する。

## リリース

1. `courseforge approve <id> --reviewer ...`
2. 特商法表示、返金条件、提供開始日を完成。
3. `courseforge publish <id> --channels stripe,shopify` で計画確認。
4. Stripe test modeで購入、リダイレクト、Webhook、メール、返金を検証。
5. `PUBLISH_MODE=live` と一致する `APPROVED_RELEASE_ID` を設定。
6. ライブ実行は一度。結果IDを `commerce-result.json` に保存。

## 購入後案内

1. Stripe署名検証済みイベントだけをSQLiteへ登録。
2. 同じイベントIDは無視し、同じメールとリリースの重複登録も防止。
3. `courseforge fulfill-pending` で送信計画を確認。
4. Resendのテストドメインで確認後、`FULFILLMENT_MODE=live` と `--live` を同時に使う。
5. 失敗した受講者はfailedとして残り、次回に再処理される。

## 障害時

- モデルAPI失敗: 同一プロバイダーを再試行せず、次の許可済み経路へ移る。
- 予算超過: 無料・ローカル経路へ落とし、審査不足なら公開停止。
- Notion 429: Retry-Afterを尊重し、最大3回で停止。
- RSS不調: その情報源だけ隔離し、他の取得を継続。
- 決済API失敗: 同じrelease IDのidempotency keyを使い、作成済み資源を確認してから再実行。
- 内容変更: 承認済み本文を直接上書きせず、新release IDを発行する。

## Secrets

APIキー、個人住所、電話番号はコミットしません。ローカル `.env`、GitHub Secrets、ホスティング先の暗号化環境変数を使います。ログ、成果物、Google Drive同期へ秘密値を出力しないでください。
