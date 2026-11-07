# テスト

## 1. 実行コマンド

```bash
# Web
pnpm --dir app/web lint
pnpm --dir app/web check:i18n
pnpm --dir app/web build
node --test app/web/tests/*.test.mjs

# API
cd app/api
uv sync --locked
uv run --no-sync pytest
uv run --no-sync pytest tests/unit
uv run --no-sync pytest tests/integration
uv run --no-sync pytest tests/e2e

# Worker
cd app/worker && uv run pytest

# Browser GPT Live E2E
pnpm test:e2e:voice
```

## 2. テスト分離

| 種類 | 対象 | 範囲 |
| --- | --- | --- |
| Unit | `app/api/tests/unit`、`app/web/tests` | 業務関数、Provider境界、UIロジック |
| Integration | `app/api/tests/integration` | API、Repository、PostgreSQL境界 |
| E2E | `app/api/tests/e2e` | 決定論的LLM Providerを使う複数ターン処理 |
| Browser | `tests/e2e/voice` | GPT Liveクライアントとブラウザー音声経路 |

通常の自動テストは実OpenAI APIを呼ばず、Providerを置き換える。実GPT Liveの確認はAPIキーとブラウザー音声環境を用意した場合に個別に行う。
