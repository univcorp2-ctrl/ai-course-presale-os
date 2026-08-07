# Notion設計

Notionは原稿置き場ではなく、情報の利用条件、検証日、承認、公開状態を管理するコントロールプレーンとして使います。

## Knowledge Sources data source

| Property | Type | Purpose |
|---|---|---|
| Title | title | 情報源・社内知識の名前 |
| Status | status | Inbox / Reference / Approved / Expired / Rejected |
| Source URL | url | 一次情報のURL |
| Source Type | select | Internal / Official / Research / News / Customer |
| Last Verified | date | 最終確認日 |
| Expires At | date | 再検証期限 |
| Allowed Use | select | Internal only / Summarize / Quote short / Publish |
| Confidentiality | select | Public / Internal / Restricted |
| Tags | multi_select | テーマ |
| Canonical Facts | rich_text | 確認済み事実。推測と分ける |
| Owner | people | 検証責任者 |

API接続には、このdata sourceをインテグレーションへ共有し、`Copy data source ID` でIDを取得します。databaseは複数data sourceのコンテナとなり、クエリは `/v1/data_sources/{id}/query` を使います。

## Content Queue data source

| Property | Type | Purpose |
|---|---|---|
| Title | title | コンテンツ名 |
| Status | status | Idea / Research / Draft / Review / Approved / Scheduled / Published / Rejected |
| Audience | select | 対象顧客 |
| Release ID | rich_text | リポジトリのリリースID |
| Content Hash | rich_text | 承認時本文との一致確認 |
| Platforms | multi_select | Direct / note / Udemy / Brain / Shopify |
| Publish At | date | 公開予定 |
| Reviewer | people | 人の最終責任者 |
| Approval Note | rich_text | 承認根拠・残課題 |
| Performance | number | 売上だけでなく完読率等も含む指標 |

## APIルール

- `Notion-Version: 2026-03-11` を固定し、移行時にテストする。
- クライアント側で間隔を空け、429は `Retry-After` を尊重する。
- integrationには必要なページだけ共有する。
- 既定ではPublicかつSummarize/Publishだけを外部モデルへ渡す。
- Restricted情報を外部モデルへ渡さない。
- 削除ではなくExpired/Rejectedへ状態変更し、監査可能性を残す。

Sources:
- https://developers.notion.com/reference/request-limits
- https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03
- https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11
