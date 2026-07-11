# Skill: elasticsearch

## 目的

Elasticsearchを中心に、ナレッジ、ドキュメント、チャット、承認、監査ログを保存・検索する。

## 設計方針

- RDBのJOINに依存しない。
- 検索に必要な表示名や権限情報は非正規化して持つ。
- index mappingを明示する。
- user_id / tenant_id / knowledge_db_id で絞り込めるようにする。
- text検索、keyword filter、vector searchを使い分ける。

## index例

- `knowledge_dbs`
- `knowledge_schemas`
- `interview_sessions`
- `interview_messages`
- `knowledge_records`
- `ai_proposals`
- `documents`
- `document_chunks`
- `document_read_status`
- `chat_sessions`
- `chat_messages`
- `audit_logs`

## 注意

- 既読状態は `documents` に埋め込まず、`document_read_status` でユーザー別に管理する。
- 取り込み状態は `documents` に保存する。
- AI提案と正式ナレッジは状態で区別する。
