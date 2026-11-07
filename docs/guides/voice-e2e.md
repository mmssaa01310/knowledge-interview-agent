# GPT Live音声E2E

音声インタビューのブラウザー経路はGPT Liveに固定されている。通常のテストはFake DataChannelやモックAPIを使い、OpenAIへの実接続を必要としない。

## テストの実行

```bash
pnpm test:e2e:voice
pnpm voice-test
```

E2Eの設定、フェイクトランスポート、シナリオは`tests/e2e/voice/`にある。通常の実行で実APIキーを読み込んだり、OpenAI APIへ接続したりしてはならない。

## 実GPT Live接続の確認

実接続テストは、アプリ全体設定画面に暗号化保存したOpenAI APIキーを使う開発環境で実行する。キーを環境変数や`VITE_*`へコピーしない。Level 3のテストは明示的に有効化した場合のみ実行し、`gpt-live-1`へのWebRTC接続、字幕受付、Interview State更新を確認する。

レポートにはAPIキー、セッション認証情報、ユーザー発話全文を含めない。実接続の失敗は通常のモックE2E結果と分けて記録する。
