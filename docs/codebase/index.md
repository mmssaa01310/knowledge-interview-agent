# AIインタビュー コードベースガイド

## 最初に読む資料

1. [正式仕様](../spec.md)
2. [アーキテクチャ](ARCHITECTURE.md)と[コードベース構成](STRUCTURE.md)
3. [技術スタック](STACK.md)、[外部連携](INTEGRATIONS.md)、[テスト](TESTING.md)

## 現在の要点

- Web、API、PostgreSQLをローカルComposeで起動する。
- テキスト生成と意味評価にはBackend経由でOpenAI Responses APIを使用する。
- 音声インタビューはWebRTCでGPT Liveへ接続する。
- OpenAI Project APIキーは管理者が設定し、Backendが暗号化して保存する。
- Workerは文書取り込み状態を返す最小サンプルである。
