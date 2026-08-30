# verification.md

# 検証ルール

## 1. 実行方針

コード変更後は、利用可能な範囲で lint / typecheck / test / build を実行する。

プロジェクトに該当コマンドが未定義の場合は、勝手に大きなツールチェーンを追加しない。
実行できない場合は、その理由を回答に明記する。

## 2. Frontend

フロントエンドを含む全体動作はDocker Composeで確認する。Web単体の型検査・翻訳検査・本番Buildは、必要に応じて個別にも実行する。

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml build web
docker compose -f infra/docker-compose.yml up --build
```

必要に応じて、プロジェクトに定義されていれば以下も実行する。

```bash
cd app/web
pnpm lint
pnpm check:i18n
pnpm build
node --test tests/*.test.mjs
```

## 3. Backend API

```bash
cd app/api
uv run pytest
```

## 4. Worker

```bash
cd app/worker
uv run pytest
```

## 5. 全体

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml build
```

## 6. PostgreSQL

通常のAPIテストは`DATABASE_URL=memory://test`のテストダブルを使う。PostgreSQL Storeの統合テストは、到達可能なPostgreSQLを`TEST_DATABASE_URL`で明示した場合だけ実行する。

```bash
cd app/api
TEST_DATABASE_URL=postgresql://... uv run pytest tests/repositories/test_postgres_store.py
```

ローカルComposeの設定とスキーマは次でも確認する。

```bash
docker compose -f infra/docker-compose.yml config
```

## 7. プロンプト分離の確認

質問項目設計まわり、実行設定まわり、プロンプト loader まわりを変更した場合は、以下を確認する。

* 質問項目設計の system prompt が `agents/question_design/prompts/*` だけで構成されているか
* 実インタビューの system prompt が `agents/interview/prompts/base.md` と追加カスタマイズの連結になっているか
* 質問項目設計チャットの request/context に実インタビュー用 `systemPrompt` を含めていないか
* 「追加カスタマイズ」「テンプレート」「実インタビュー用」相当のUI文言が、質問項目設計チャット側に誤誘導していないか
* プロンプト分離を担保するテストが壊れていないか

## 8. フロントエンド変更時の確認

フロントエンドを変更した場合は、以下を確認する。

* Docker 経由で画面反映を確認できるか
* 開発サーバーが古い画面を出していないか
* 必要に応じて開発サーバーを再起動したか
* ブラウザキャッシュの影響がないか
* ユーザーが確認すべきURLを回答に明記したか

画面反映を確認できない場合は、未確認であることを明記する。

必要に応じて以下の確認手順も回答に含める。

* ブラウザ更新
* キャッシュクリア
* 開発サーバー再起動
* コンテナ再起動

## 9. ドキュメント変更時の確認

```bash
uv run --group dev mkdocs build --strict
python .github/skills/codebase-visualizer/scripts/validate_dashboard.py docs/codebase/dashboard
```

MkDocsの表示内容を変更した場合は、ローカルで`uv run --group dev mkdocs serve`を起動してナビゲーションも確認する。
