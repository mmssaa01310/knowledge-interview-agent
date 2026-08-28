# AGENTS.md

このリポジトリは「AIインタビュー / ナレッジ構造化アプリ」を実装するためのリポジトリです。

AIエージェント、GitHub Copilot、Cursor、Codex、Claude Code等は、このファイルをリポジトリ全体の最上位ルールとして扱ってください。

## 1. 最優先ルール

* ユーザーの直近の明示指示を最優先する。
* プロダクト仕様の正本は`docs/spec.md`とする。
* `docs/`と`specs/`の扱いは`docs/reference/spec-governance.md`に従う。
* 仕様にない機能、画面、ライブラリ、AWSサービス、DBを勝手に追加しない。
* 既存の設計、命名、フォルダ構成、実装パターンに合わせる。
* 依頼された範囲だけを変更する。
* 依頼外の大規模リファクタリング、広範囲フォーマット、命名変更、ディレクトリ移動はしない。
* 秘密情報、認証情報、トークン、`.env`をコミットしない。
* 共有Agent Skillsの正本は`.github/skills/`とし、`.agents/skills`はローカル互換リンクとして編集しない。
* 回答は日本語で行う。

## 2. ドキュメント

変更内容に応じて、以下の正本を参照・更新する。

* プロダクト仕様: `docs/spec.md`
* システム構成: `docs/architecture/`
* 個別AIエージェント仕様: `docs/agents/`
* 開発手順: `docs/guides/`
* 共通ルール・技術情報: `docs/reference/`
* 未実装機能の計画: `docs/plans/`

同じ内容を複数ファイルに重ねて維持しない。

`docs/plans/`は実装計画であり、確定仕様として扱わない。

## 3. docsとspecs

* `docs/`はプロダクト全体の正規ドキュメントとする。
* `specs/`は実装中の変更単位の作業仕様だけを置く。
* 両者が矛盾する場合は、原則として`docs/`を正とする。
* 現在の依頼で`specs/<id>`が明示されている場合のみ、その内容を作業指示として扱う。
* 実装完了後は、恒久的な内容を`docs/`へ反映し、完了済みの`specs/<id>`を削除する。
* 変更履歴はGit履歴で管理し、完了済みSpecを現行ドキュメントとして残さない。

## 4. 実装方針

* 小さく安全に変更する。
* 既存のservice、repository、schema、agentを再利用し、類似ロジックを複製しない。
* UI、API、状態管理、型、バリデーション、ビジネスロジックを必要に応じて分離する。
* 認証ユーザーIDを保存データに含める。
* 認可チェックを省略しない。
* AI出力を人の承認なしに正式ナレッジ化しない。
* 外部サービス固有処理と業務ロジックを混在させない。

AI関連処理は以下に従う。

* `docs/architecture/agents/agent-architecture.md`
* `docs/agents/agent-behavior-policy.md`
* `docs/agents/interview-agent-strands.md`
* `docs/agents/question-design-agent-strands.md`

Strands Agentでは「判断はAI、保証はbackend」を原則とする。
挨拶辞書、キーワード一致、文字数などの固定ルールでAI判断を過剰に置き換えない。

## 5. リアルタイム音声

リアルタイム音声機能は以下に従う。

* `docs/architecture/voice/realtime-voice.md`
* `docs/plans/realtime-voice-v1.md`

基本原則:

* 音声機能は既存Interview Agentの別入出力経路として扱う。
* 質問進行、回答評価、RAG、状態更新の正本は`app/api`に置く。
* `app/voice`にインタビュー評価やRAGを複製しない。
* `app/voice`から`app/api`のPythonモジュールを直接importしない。
* `app/api`にNova Sonic、aiortc、PyAV、FFmpeg等の音声固有依存を追加しない。
* Nova SonicとTranscribe + Pollyは共通Runtime契約の下で分離する。
* 質問作成エージェントは音声対応の変更対象に含めない。

## 6. 相談と実装依頼

「原因わかる？」「方針どうする？」「調べて」「設計としてどう？」などの相談では、原則としてコードを変更しない。

コードやドキュメントを変更するのは、ユーザーが「修正して」「実装して」「反映して」「更新して」などと明示した場合とする。

会話要約、過去の提案、一時的な仮説、`docs/plans/`の未確定事項を、明示指示なしに実装要件として扱わない。

## 7. 事前確認が必要な変更

以下は、ユーザーから明示的に指示されていない限り、実装前に方針を確認する。

* backendの分岐やfallback追加
* API形式や状態遷移の変更
* 認証・認可の変更
* データ保存形式やDB構造の変更
* LLM出力の後処理変更
* UI表示条件の変更
* 新しいAWSサービスやライブラリの追加
* サービスやデプロイ単位の追加

## 8. パッケージ管理と検証

* Frontendはpnpm workspaceを使用する。
* Pythonはuvを使用する。
* 未使用ライブラリを追加しない。
* 変更後は、変更範囲に応じてテスト、型チェック、lint、buildを実行する。
* 実行していない検証を成功したと報告しない。
* 確認できていない動作を対応済みと断定しない。

詳細:

* `docs/guides/package-management.md`
* `docs/guides/verification.md`
* `docs/reference/response-format.md`

## 9. 禁止事項

* 仕様にないDBやAWSサービスを追加する。
* Elasticsearchを別DBへ無断で置き換える。
* フロントエンドを別フレームワークへ変更する。
* 認証なしAPIを作る。
* ログインユーザーに紐づかない保存を行う。
* AI出力を自動で正式ナレッジ化する。
* 依頼外の大規模リファクタリングを行う。
* 既存コードを広範囲にフォーマットする。
* 実装計画を確定仕様として扱う。
