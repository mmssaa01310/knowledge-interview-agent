# KIKIORI ドキュメント

KIKIORIは、インタビューを通じて知識を引き出し、人の確認を経て構造化ナレッジとして残すWebアプリです。

## 読む順番

| 目的 | 読む資料 |
| --- | --- |
| 利用者向けの振る舞いを確認する | [KIKIORI仕様](spec.md) |
| **現在コードで動く内容**を確認する | [現行実装](reference/current-implementation.md) |
| システム構成・認可境界を理解する | [全体構成と実装状況](architecture/aws/aws-architecture.md)、[認可](architecture/access-control.md) |
| ローカルで起動・検証する | [開発ガイド](guides/development-workflow.md)、[検証](guides/verification.md) |
| コードの入口を短時間で把握する | [コードベースガイド](codebase/index.md) |

## 文書の役割

* `spec.md`はプロダクトが満たすべき振る舞いを定義します。
* `reference/current-implementation.md`は、現行コードで確認できる実装範囲を示します。実装状況の確認ではこちらを優先します。
* `architecture/`は責務境界と設計方針、`guides/`は開発・検証手順を扱います。
* `plans/`は未確定の検討資料です。実装済み機能の根拠には使いません。
* `codebase/`はソースコードから再調査した技術者向けの案内です。

文書と実装に差異を見つけた場合は、まずコードとテストを確認し、恒久的な仕様変更なら`spec.md`と関連設計へ反映します。運用ルールの詳細は[docsとspecsの管理](reference/spec-governance.md)を参照してください。
