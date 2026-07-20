# spec-governance.md

## 1. 目的

このリポジトリでは、`docs/` と `specs/` の役割を分けて管理する。
仕様の重複や矛盾により、AIエージェント、GitHub Copilot、Codex、Claude Code などが誤った実装判断をしないようにする。

## 2. docs の役割

`docs/` は、プロダクト全体の正規ドキュメントを置く場所である。

主な対象は以下。

* アプリ全体の仕様
* 業務ルール
* 技術スタック
* AWSアーキテクチャ
* リポジトリ構成
* API仕様
* 開発ルール
* 検証ルール

代表例。

* `docs/spec.md`
* `docs/technology-stack.md`
* `docs/aws-architecture.md`
* `docs/repository-structure.md`
* `docs/development-workflow.md`
* `docs/verification.md`
* `docs/response-format.md`
* `docs/package-management.md`

## 3. specs の役割

`specs/` は、Spec Kit などで作成する変更単位の作業仕様を置く場所である。

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
3. `docs/technology-stack.md`
4. `docs/aws-architecture.md`
5. `docs/repository-structure.md`
6. `docs/development-workflow.md`
7. `docs/verification.md`
8. 作業対象の `specs/<id>/spec.md`
9. 作業対象の `specs/<id>/plan.md`
10. 作業対象の `specs/<id>/tasks.md`
11. 古い `specs/` 配下のドキュメント

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

`specs/<id>` の作業が完了したら、恒久的な仕様変更だけを `docs/` に反映する。

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

## 8. 古い specs の扱い

完了済みの `specs/` は、現在の正規仕様として扱わない。

完了済み `specs/` は、過去の変更履歴・実装経緯として参照する。

古い `specs/` と `docs/` が矛盾する場合は、必ず `docs/` を優先する。

## 9. 禁止事項

* `docs/spec.md` と同じ内容を `specs/` に重複して長く書かない。
* 古い `specs/` を正規仕様として扱わない。
* `specs/` だけを更新して、恒久仕様を `docs/` に反映し忘れない。
* `docs/` と `specs/` の矛盾を放置しない。
* 作業対象外の `specs/` を根拠に実装を変更しない。
