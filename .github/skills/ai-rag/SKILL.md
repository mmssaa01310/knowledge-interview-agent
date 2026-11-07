---
name: ai-rag
description: OpenAI Responses APIを使ったAIインタビュー、構造化抽出、ナレッジ参照の実装・変更時に使用する。
---

# Skill: ai-rag

## 目的

BackendのOpenAI Responses APIとStructured Outputsを使い、インタビュー、構造化抽出、質問設計を実装する。

## 原則

- AI出力は下書きであり、正式ナレッジではない。
- AI提案は `draft` または `needs_review` として保存する。
- 人の承認後のみ検索・チャット回答の正式参照対象にする。
- 検索ではユーザー権限・参照設定・文書対象を必ずfilterする。
- AI回答には参照元を保持する。
- OpenAI SDK呼び出しはBackend Providerへ集約し、APIキーをBrowserやログへ出さない。

## 処理フロー

1. ユーザー発話を保存する。
2. 必要に応じて過去記録・ドキュメントを検索する。
3. Backend ProviderからOpenAI Responses APIを呼び出す。
4. Structured Outputを検証し、必要な場合はSSEで返す。
5. 回答完了後、構造化提案を生成する。
6. AI提案カードとして保存する。
7. ユーザーが修正・承認する。
