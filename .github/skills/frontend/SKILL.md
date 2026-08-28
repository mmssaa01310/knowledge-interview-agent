---
name: frontend
description: ReactとViteでAIインタビューアプリの画面、状態管理、API連携を実装・変更するときに使用する。
---

# Skill: frontend

## 目的

React/ViteでAIインタビューアプリの画面を実装する。

## 基本構成

- `pages/`: 画面単位
- `features/`: ドメイン別機能
- `components/`: 汎用UI
- `hooks/`: カスタムhooks
- `lib/api`: APIクライアント
- `types/`: 型定義

## 実装ルール

- UIとAPI呼び出しを分ける。
- フォームはReact Hook Form + Zodで検証する。
- API通信はTanStack Queryを使う。
- チャットのストリーム描画はSSEを使う。
- AI提案カードは通常メッセージとは別に扱う。
- 承認操作は楽観更新しすぎず、API結果を確認して表示更新する。
- 全承認は確認ダイアログを必ず表示する。
- ローディング、エラー、空状態を実装する。

## App.tsxの責務

- `App.tsx` に画面実装を詰め込まない。
- `App.tsx` は Provider / Router / AppShell の呼び出しに留める。
- 画面コンポーネントは `pages/` に配置する。
- 業務機能単位の部品は `features/` に配置する。
- API通信、SSE、承認処理、フォーム処理を `App.tsx` に直接書かない。
- 1ファイルが大きくなりすぎた場合は、責務単位で分割する。

## 主要画面

- ログイン画面
- ナレッジDB一覧
- ナレッジ項目設定
- ドキュメント追加・一覧
- AIインタビュー記録一覧
- AIインタビュー画面
- ナレッジ参照チャット
- 参照設定
