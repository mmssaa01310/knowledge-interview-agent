# 実装規約

## 1. 命名と責務

- React ComponentはPascalCaseの`.tsx`、TypeScript関数・機能モジュールはcamelCaseを使う。
- Pythonはsnake_caseのモジュールと関数、PascalCaseの型を使う。
- Webは機能単位の`features/`、APIは`routers` / `services` / `repositories` / `agents`で分ける。
- OpenAI SDK呼び出しはProviderに集約し、HTTP routerから直接呼ばない。

## 2. エラーとログ

- AI Providerの失敗を業務状態へ反映しない。
- APIキーや会話本文などの秘密情報をログに出さない。
- APIエラーは既存の共通エラー形式に合わせる。

## 3. テスト

- 通常のテストではOpenAI APIへ接続せず、Provider境界をstub/mockする。
- 状態遷移、認可、テナントスコープをBackendで検証する。
