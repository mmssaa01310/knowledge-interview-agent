# package-management.md

## 1. 基本方針

このリポジトリは pnpm workspace を使用する。

Node.js / フロントエンド関連の依存関係は、npm / yarn ではなく pnpm を前提にする。

## 2. 正とするファイル

pnpm workspace の正規設定は以下とする。

* `pnpm-workspace.yaml`
* `pnpm-lock.yaml`
* root `package.json`
* 各 workspace の `package.json`

## 3. pnpm-workspace.yaml

workspace 定義と pnpm 固有設定は `pnpm-workspace.yaml` に書く。

```yaml
packages:
  - "app/web"
  - "packages/shared-types"
  - "infra/cdk"

allowBuilds:
  esbuild: true
```

pnpm 11 では `onlyBuiltDependencies` は使わない。
`ERR_PNPM_IGNORED_BUILDS` が出た場合は、必要な依存だけ `pnpm-workspace.yaml` の `allowBuilds` に追加する。
esbuild は Vite / React ビルドで必要になるため、`allowBuilds.esbuild = true` を許可する。

## 4. root package.json

root `package.json` には、以下だけを基本とする。

* `name`
* `private`
* `version`
* `packageManager`
* `scripts`

`workspaces` は root `package.json` ではなく `pnpm-workspace.yaml` に書く。
`pnpm.onlyBuiltDependencies` は root `package.json` に書かない。
pnpm 11 の build script 承認は `pnpm-workspace.yaml` の `allowBuilds` で管理する。

## 5. pnpm 11 の build script 承認

pnpm 11 では、build script を必要とする依存関係の承認に `allowBuilds` を使う。

例:

```yaml
allowBuilds:
  esbuild: true
```

`onlyBuiltDependencies` を新規に追加したり、root `package.json` の `pnpm` フィールドへ戻したりしない。

## 6. ERR_PNPM_IGNORED_BUILDS が出た場合の確認手順

以下の順に確認する。

1. エラーメッセージに出ている依存名を確認する。
2. その依存が本当に build script を必要とするか確認する。
3. 必要な依存だけを `pnpm-workspace.yaml` の `allowBuilds` に追加する。
4. `pnpm-lock.yaml` と workspace 設定がずれていないか確認する。
5. Docker build ならキャッシュの影響を避けて再実行する。

`esbuild` であれば、まず `allowBuilds.esbuild: true` を確認する。

## 7. Docker build 時の注意

Docker build 中に `pnpm install --frozen-lockfile` が失敗した場合は、`docker-compose.yml` や Dockerfile だけで判断しない。

必ず以下も確認する。

* `pnpm-workspace.yaml`
* `pnpm-lock.yaml`
* root `package.json`
* `app/web/package.json`
* `app/web/Dockerfile.dev`
* `infra/docker-compose.yml`

確認用の代表コマンド:

```bash
docker compose -f infra/docker-compose.yml build --no-cache web
docker compose -f infra/docker-compose.yml up --build
```

## 8. 禁止事項

* npm / yarn に勝手に変更しない。
* lockfile を理由なく削除しない。
* `pnpm install --no-frozen-lockfile` を安易に使わない。
* Docker build 失敗時に、原因確認なしで依存関係を追加しない。
* pnpm 11 の build script 承認を root `package.json` の `pnpm` フィールドで管理しない。
* `onlyBuiltDependencies` の古い説明を新規ドキュメントや設定へ持ち込まない。

## 9. Python / uv

このリポジトリの Python パッケージ管理は uv を標準とする。

依存関係の作成・同期・テスト実行は、原則として uv 経由で行う。

### 標準コマンド

```bash
cd app/api
uv run pytest
uv run ruff check .
uv run mypy src
```

### 同期を避けたい場合

`uv run` は実行前に lock / sync を伴うため、ネットワーク不安定時や依存同期が不要な検証では余計な失敗要因になる。

その場合は `uv run --no-sync` を優先する。

```bash
cd app/api
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy src
```

表現は「uv が使えない」ではなく、「uv run の同期がネットワーク依存になるため、今回は同期を避けた」とする。

### `.venv` 直接実行の扱い

既存の `.venv` 直接実行はフォールバック扱いとする。

`uv run --no-sync` でも実行できない場合のみ、既存の `app/api/.venv/bin/python` を使ってよい。

```bash
app/api/.venv/bin/python -m pytest app/api/tests/contract/test_mvp_flow.py
```

`.venv` 直接実行を使った場合は、回答に必ず以下を明記する。

* `uv run` を使わなかった理由
* `.venv` が既存環境であること
* 実行したコマンド
* 未確認リスク
