# spec-governance.md

## 1. 目的

このリポジトリでは、`docs/` と `specs/` の役割を分けて管理する。
実装済みの情報を`docs/`へ集約し、作業中の仕様だけを`specs/`に置くことで、AIエージェント、GitHub Copilot、Codex、Claude Codeなどが古い作業資料を現行仕様と誤認しないようにする。

## 2. docs の役割

`docs/` は、プロダクト全体の正規ドキュメントを置く場所である。

主な対象は以下。

* アプリ全体の仕様
* 業務ルール
* 技術スタック
* AWSアーキテクチャ
* リポジトリ構成
* 現行実装の範囲とAPI・データモデル
* API仕様
* 開発ルール
* 検証ルール

代表例。

* `docs/spec.md`
* `docs/reference/technology-stack.md`
* `docs/architecture/aws/aws-architecture.md`
* `docs/reference/repository-structure.md`
* `docs/reference/current-implementation.md`
* `docs/guides/development-workflow.md`
* `docs/guides/verification.md`
* `docs/reference/response-format.md`
* `docs/guides/package-management.md`

## 3. specs の役割

`specs/` は、Spec Kitなどで作成する実装中の変更単位の作業仕様を置く場所である。

完了済みの実装を管理する場所ではない。完了後も維持する仕様、設計、API、データモデルは`docs/`へ反映する。

主な対象は以下。

* 特定機能の追加
* 特定画面の変更
* 特定APIの実装
* 特定バグ修正
* 特定リファクタリング
* 実装計画
* タスク分解
* 受け入れ条件
* 検証観点

例。

```text
specs/
  001-knowledge-db-crud/
    spec.md
    plan.md
    tasks.md
  002-ai-interview-sse/
    spec.md
    plan.md
    tasks.md
```

## 4. 正本の優先順位

仕様が競合した場合は、以下の順に優先する。

1. `AGENTS.md`
2. `docs/spec.md`
3. `docs/reference/technology-stack.md`
4. `docs/architecture/aws/aws-architecture.md`
5. `docs/reference/repository-structure.md`
6. `docs/guides/development-workflow.md`
7. `docs/guides/verification.md`
8. 作業対象の `specs/<id>/spec.md`
9. 作業対象の `specs/<id>/plan.md`
10. 作業対象の `specs/<id>/tasks.md`

完了済み`specs/`は優先順位の対象外とする。

## 5. 競合時の扱い

`docs/` と `specs/` が矛盾する場合は、原則として `docs/` を正とする。

ただし、現在の作業依頼で明示的に `specs/<id>` が指定されている場合は、その `specs/<id>` を作業指示として扱う。

その場合でも、`docs/spec.md` の業務ルールや禁止事項に反する実装は行わない。

## 6. specs を作る条件

`specs/` は、以下のような変更で作成する。

* 複数ファイルにまたがる変更
* 画面、API、データ構造が同時に変わる変更
* 受け入れ条件を明確にしたい変更
* 実装タスクを段階的に分けたい変更
* AIエージェントにまとまった実装作業を依頼する変更

小さな修正、軽微な文言変更、単純なバグ修正では必須ではない。

## 7. specs 完了後の反映

`specs/<id>`の作業が完了したら、恒久的な情報を`docs/`へ反映する。その後、完了済みの`specs/<id>`を削除する。

反映対象の例。

* 新しい業務ルール
* 新しいAPI
* 新しい状態値
* 新しい権限ルール
* 新しいAWS構成
* 新しいリポジトリ構成
* 今後も守るべき実装方針

反映しないものの例。

* 一時的な実装メモ
* 試行錯誤の履歴
* タスク一覧
* 作業中の仮説
* すでに完了したチェックリスト

反映先の目安は次のとおりとする。

* プロダクトの振る舞い・業務ルール: `docs/spec.md`
* システム構成・AI責務・データフロー: `docs/architecture/`
* 現行コードで実装済みの範囲、API、データモデル: `docs/reference/current-implementation.md`
* 開発手順・検証手順: `docs/guides/`

## 8. 完了済み specs の扱い

完了済みの`specs/<id>`は、`docs/`への反映確認後に削除する。

過去の作業内容が必要な場合はGit履歴を参照する。完了済みSpecを別の現行ドキュメントとして複製しない。

`specs/`のルートディレクトリは、次の変更作業で再利用するため残してよい。

## 9. 禁止事項

* `docs/spec.md`と同じ内容を`specs/`に重複して長く書かない。
* 実装完了後も`specs/<id>`を現行仕様として残さない。
* `specs/`だけを更新して、恒久情報を`docs/`に反映し忘れない。
* `docs/`と作業中の`specs/<id>`の矛盾を放置しない。
* 作業対象外の`specs/`を根拠に実装を変更しない。
