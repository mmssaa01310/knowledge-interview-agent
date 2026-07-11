# Research: AI Interview Knowledge Capture Current Baseline

## Decision 1: Development-token authentication first

- **Decision**: 認証は `x-dev-token` または Bearer の開発トークンで行う。
- **Rationale**: ローカルで Cognito なしに UI と API の導線を早く検証できる。
- **Alternatives considered**:
  - 最初から Cognito 実接続: ローカル確認コストが高い。

## Decision 2: In-memory persistence for current baseline

- **Decision**: API 永続化は `InMemoryStore` を使う。
- **Rationale**: CRUD 導線、承認、文書状態、参照チャットの基本動作を先に固められる。
- **Alternatives considered**:
  - 早期に Elasticsearch 接続: 現段階では開発と検証の摩擦が大きい。

## Decision 3: Knowledge DB contains knowledges, and knowledges contain records/documents/fields

- **Decision**: ドメイン単位は `KnowledgeDb -> Knowledge -> {KnowledgeField, InterviewRecord, Document}` の3層にする。
- **Rationale**: 現在の画面遷移と API ルートがこの構造を前提としている。
- **Alternatives considered**:
  - KnowledgeDb 直下に fields/records/documents を置く: 現行コードと一致しない。

## Decision 4: Mock interview stream and proposal generation

- **Decision**: 記録メッセージ送信時は提案を保存し、SSE は固定イベント列を返す。
- **Rationale**: UI 側のストリーム体験と承認フローを先に完成させるため。
- **Alternatives considered**:
  - 最初から本番AIストリーム: 実装と運用の依存が重い。

## Decision 5: Reference chat uses selected active resources only

- **Decision**: 参照チャットは選択済み ID のうち、`active` なナレッジDB・ナレッジと `completed` の文書だけを文脈に含める。
- **Rationale**: 現行データモデルに approval 状態の正式反映がまだないため、最低限の絞り込みとして status ベースにしている。
- **Alternatives considered**:
  - 承認済みナレッジのみ検索: 現在の永続モデルでは未実装。

## Decision 6: Local chatbot settings in frontend state

- **Decision**: チャットボット設定はサーバー保存せず、フロントのローカル状態で保持する。
- **Rationale**: 参照チャット UI を早く検証できる。
- **Alternatives considered**:
  - API 永続化: 現段階ではデータモデル追加コストが高い。
