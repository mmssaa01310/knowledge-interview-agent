# AGENTS.md

このリポジトリは「AIインタビュー / ナレッジ構造化アプリ」を実装するためのリポジトリです。
AIエージェント、GitHub Copilot、Cursor、Codex、Claude Code 等は、このファイルを最上位ルールとして扱ってください。

## 1. 最優先ルール

* ユーザーの直近の明示指示を最優先する。
* 仕様の正本は `docs/spec.md` とする。
* `docs/` と `specs/` の扱いは `docs/spec-governance.md` に従う。
* 仕様にない機能、画面、ライブラリ、AWSサービス、DBを勝手に追加しない。
* 既存の設計、命名、フォルダ構成に合わせる。
* 依頼された範囲だけを変更する。
* 依頼外の大規模リファクタリング、広範囲フォーマット、命名変更、ディレクトリ移動はしない。
* 秘密情報、認証情報、`.env` をコミットしない。
* 回答は日本語で行う。

## 2. 正本ドキュメント

詳細ルールは以下を参照する。

* `docs/spec.md`: 機能仕様、業務ルール、状態管理、承認仕様
* `docs/spec-governance.md`: `docs/` と `specs/` の役割、優先順位、競合時ルール
* `docs/technology-stack.md`: 技術スタック
* `docs/aws-architecture.md`: AWS構成
* `docs/agent-architecture.md`: AIエージェントの責務分離
* `docs/repository-structure.md`: フォルダ構成、責務分離
* `docs/development-workflow.md`: 実装前後の進め方
* `docs/verification.md`: 確認項目、実行コマンド
* `docs/response-format.md`: 回答フォーマット
* `docs/package-management.md`: pnpm workspace、Docker build、Node.js依存管理
* `docs/python-development.md`: Python、uv、`.venv` の運用
* `docs/refactoring.md`: リファクタリング方針
* `docs/agent-change-control.md`: AIエージェントの変更管理

同じ内容を複数ファイルに重ねて維持しない。
仕様追加や構成変更があれば、責務を持つ正本だけを更新する。

## 3. docs と specs の扱い

* `docs/` はプロダクト全体の正規ドキュメントとして扱う。
* `specs/` は変更単位の作業仕様として扱う。
* `docs/` と `specs/` が矛盾する場合は、原則として `docs/` を正とする。
* 現在の作業依頼で `specs/<id>` が明示されている場合のみ、その `specs/<id>` を作業指示として読む。
* 完了済みの古い `specs/` は、正規仕様ではなく過去の変更履歴として扱う。

## 4. 実装方針

* 小さく変更する。
* 1ファイルに複数責務を詰め込まない。
* UI、API呼び出し、状態管理、型定義、バリデーション、ビジネスロジックを必要に応じて分離する。
* 認証ユーザーIDを保存データに含める。
* 認可チェックを省略しない。
* AI出力を人の承認なしに正式ナレッジ化しない。
* リファクタリングは外部仕様を変えず、小さく安全に行う。
* AI関連処理は `docs/agent-architecture.md` の責務分離に従う。
* 質問設計エージェント、インタビューエージェント、暗黙知回答エージェントを混同しない。
* 質問設計エージェントはユーザーに答えを聞くのではなく、熟練者に聞く質問項目を設計する。
* 暗黙知回答エージェントは回答専用であり、DB更新してはいけない。

## 5. 相談と実装依頼の区別

以下のような依頼は、原則としてコード変更しない。

* 「原因わかる？」
* 「おかしくない？」
* 「方針どうする？」
* 「調べて」

この場合は、まず原因分析、影響範囲、修正案を回答する。
コード変更は、ユーザーが明示的に「修正して」「実装して」「反映して」と依頼した場合のみ行う。

## 6. 要件として扱ってはいけないもの

以下は、明示的な実装指示がない限り、要件として扱ってはいけない。

* 会話要約
* Continuation Plan
* assistantの過去の提案
* pending task
* 推測した改善案
* 一時的なデバッグ仮説
* 相談中の「こうするとよい」という案

これらを根拠にコードを変更してはいけない。

## 7. 事前確認が必要な変更

以下の変更は、実装前に変更方針を提示し、ユーザーの承認を得る。

* backendの分岐条件追加
* fallbackロジック追加
* APIレスポンス形式の変更
* 状態遷移の変更
* 認証・認可の変更
* データ保存形式の変更
* LLM出力の後処理変更
* UI表示条件の変更
* テスト期待値に合わせた実装変更

特に、prompt調整で済む可能性がある問題に対して、backend固定ロジックを勝手に追加してはいけない。

## 8. LLM挙動修正の切り分け

LLMの応答がおかしい場合は、原因を切り分ける。

* promptの問題
* Bedrock呼び出し失敗
* Bedrockの出力形式崩れ
* JSON parse failure
* backendの後処理問題
* frontendの表示条件問題

原因が未確定のまま、backendにdeterministicな固定ルールを追加してはいけない。
まずはログ追加、出力観察、prompt最小修正、テスト追加を優先する。

## 9. パッケージ管理

* Node.js / Frontend は pnpm workspace を使用する。
* pnpm workspace と build script 承認の正規設定は `pnpm-workspace.yaml` とする。
* pnpm 11 では `onlyBuiltDependencies` ではなく `allowBuilds` を使う。
* Pythonパッケージ管理はuvを標準とする。
* ネットワーク不安定時や同期不要の検証では `uv run --no-sync` を優先する。
* 詳細は `docs/package-management.md` と `docs/python-development.md` を参照する。

## 10. 絶対に避けること

* 仕様にないDBを追加する。
* ElasticsearchをPostgreSQLやDynamoDBに置き換える。
* Next.jsへ変更する。
* CloudFront / S3フロント配信を追加する。
* EventBridgeをMVPに追加する。
* 認証なしAPIを作る。
* ログインユーザーに紐づかない保存をする。
* AI出力を自動で正式ナレッジ化する。
* 依頼外の大規模リファクタリングをする。
* 既存コードを広範囲にフォーマットする。
* 未使用ライブラリを追加する。
