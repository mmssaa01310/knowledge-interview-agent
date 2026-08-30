# 注意点と未確定事項

## 1. 優先度の高い注意点

| 重要度 | 内容 | 根拠 | 影響 | 次の対応 |
| --- | --- | --- | --- | --- |
| HIGH | 本番認証が未実装 | `auth/deps.py`は固定開発トークンのみを検証 | 開発用認証のまま本番公開できない | 本番IdP・JWT検証・秘密情報運用を確定し実装する |
| HIGH | 実AWS / WebRTCのE2Eが自動化されていない | `app/voice/tests/`はunit / contract中心 | 実機のネットワーク、認証、音声遅延の退行を検出できない | 別環境でsmoke / browser E2Eを設計する |
| MEDIUM | WorkerとSQSが未接続 | `app/worker`はサンプル処理、ComposeにWorkerなし | 文書取り込みを非同期実行できない | 要件確定後にQueue契約・永続化・再試行を実装する |
| MEDIUM | JSONB互換Storeへの集約 | `repositories/store.py` | データ量・検索要求によりクエリとスキーマが複雑化する | 利用量を測定し、必要な論理エンティティから正規化を検討する |
| LOW | 認証設定名の旧称が残存 | `core/config.py`に`COGNITO_*`環境変数名が残るが、認証判定では未使用 | 本番IdP未確定のまま、設定名だけで採用サービスを誤認しやすい | IdPを決定した時点で設定名・UI文言・認証実装を一括確認する |

## 2. 技術的負債

| 項目 | 場所 | 放置した場合のリスク | 対応 |
| --- | --- | --- | --- |
| DBスキーマ定義の二重化 | `repositories/store.py`、`infra/postgres/init/001_schema.sql` | 片方だけ更新して差異が生じる | migrationの正本と実行方針を決める |
| Workerの最小実装 | `app/worker/` | 設計文書の目標構成と実装範囲を混同しやすい | 実装するまで文書で「未実装」と明示する |
| Webのテスト範囲が限定的 | `app/web/tests/` | UIの回帰を型検査だけで見逃す | 重要な画面操作からテスト方針を追加する |

## 3. セキュリティ

| リスク | 根拠 | 現在の対策 | 不足 |
| --- | --- | --- | --- |
| 開発トークンの誤使用 | `auth/deps.py` | 無効トークンは401 | 本番JWT検証、トークン配布・失効 |
| 内部API共有トークン | `core/config.py`、`internal_voice.py` | 環境変数化 | 本番のサービス間認証・ローテーション |
| 秘密情報の混入 | `.env.example`、`infra/docker-compose.yml` | `.env`をコミットしない運用 | CIでの秘密情報検査は未確認 |

## 4. 性能・スケール

| 懸念 | 根拠 | 対応の方向 |
| --- | --- | --- |
| Store操作ごとの新規PostgreSQL接続 | `PostgresStore._connection()` | 負荷計測後に接続プールを検討する |
| JSONB payload検索 | `entity_store`と式インデックス | 実クエリを測定し、必要なインデックスや正規化を追加する |
| 音声の実ネットワーク依存 | WebRTC / AWS音声Runtime | 実端末・ネットワーク別のsmokeを継続する |

## 5. 変更が慎重な領域

| 領域 | 理由 | 安全な変更方法 |
| --- | --- | --- |
| `services/voice_interview.py` | Voice Turn、回答評価、状態遷移が集まる | API/Voice contract testを先に実行する |
| `repositories/store.py` | 全論理エンティティの保存契約 | memory / PostgreSQL両方のテストを確認する |
| `app/web/src/routes/useKnowledgeWorkspaceController.ts` | ワークスペースの画面状態とAPI操作が集まる | ロール別・viewport別に手動確認する |

## 6. [ASK USER] 確認したい事項

1. [ASK USER] 本番IdPはMicrosoft Entra IDを候補として調査する方針でよいですか。採用決定までは開発用トークンの現状を維持します。

## 根拠

* `app/api/src/ai_interviewer_api/auth/deps.py`
* `app/api/src/ai_interviewer_api/core/config.py`
* `app/api/src/ai_interviewer_api/repositories/store.py`
* `app/api/src/ai_interviewer_api/services/voice_interview.py`
* `app/worker/src/ai_interviewer_worker/main.py`
* `infra/docker-compose.yml`
