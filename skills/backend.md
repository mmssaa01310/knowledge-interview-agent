# Skill: backend

## 目的

FastAPIで認証、ナレッジ管理、AIインタビュー、承認、ドキュメント管理、チャットAPIを実装する。

## レイヤー

- API route: HTTP入出力
- Schema: Pydantic request/response
- Service: 業務ロジック
- Repository: Elasticsearchアクセス
- Core: 認証、設定、ログ、例外

## 実装ルール

- routeに業務ロジックを書かない。
- ServiceはRepositoryを経由してデータアクセスする。
- RepositoryはElasticsearchクエリの詳細を隠蔽する。
- 認証ユーザー情報を依存性注入で受け取る。
- 認可チェックをService層で行う。
- 監査ログは承認・修正・削除・外部送信で保存する。
- エラーレスポンスは一貫した形式にする。

## 重要API

- ナレッジDB CRUD
- ヒアリング項目 CRUD
- インタビューセッション CRUD
- AIメッセージ送信
- SSEストリーム
- AI提案作成
- 個別承認
- 全承認
- 一括承認
- ドキュメントアップロード
- 取り込み状態取得
- 既読・確認済み更新
