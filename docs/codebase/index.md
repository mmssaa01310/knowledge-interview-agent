# KIKIORI コードベースガイド

このガイドは、現行ソースコードと設定ファイルから確認できる事実をまとめた技術者向け資料です。プロダクトの目標は[仕様](../spec.md)、実装済み機能の説明は[現行実装](../reference/current-implementation.md)を参照してください。

## 最初に読む資料

1. [ビジュアルマップ](dashboard/index.html)で、Browserから保存までの大まかな流れを把握する。
2. [アーキテクチャ](ARCHITECTURE.md)で、Web・API・Voice・PostgreSQLの責務境界を確認する。
3. [外部連携](INTEGRATIONS.md)と[注意点](CONCERNS.md)で、開発用認証、AWS依存、未実装のWorkerを確認する。
4. [テスト](TESTING.md)で、実行できる検証と外部依存を要する検証を確認する。

## 現在の要点

* WebはReact/Vite、APIとVoiceはFastAPI、保存先はPostgreSQLである。
* APIが認可、インタビュー状態、AI処理、保存の正本であり、Voiceは音声I/Oに限定する。
* 開発時の認証は固定の開発用トークンである。本番IdP（Entra ID候補）の検証は現行コードにはない。
* Workerは文書取り込み状態を返す最小実装で、SQSや永続化は未接続である。

## 根拠

* `app/web/package.json`
* `app/api/src/ai_interviewer_api/main.py`
* `app/api/src/ai_interviewer_api/repositories/store.py`
* `app/voice/src/ai_interviewer_voice/main.py`
* `app/worker/src/ai_interviewer_worker/main.py`
* `infra/docker-compose.yml`
