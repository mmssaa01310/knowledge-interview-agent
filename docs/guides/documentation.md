# ドキュメント運用

## 1. 目的

KIKIORIの仕様、現行実装、設計、開発手順を同じ`docs/`配下で見つけられる状態に保つ。

## 2. 文書の置き場所

| 内容 | 置き場所 |
| --- | --- |
| 利用者・プロダクトの振る舞い | `docs/spec.md` |
| 現行コードで実装済みの範囲 | `docs/reference/current-implementation.md` |
| 責務境界・データフロー | `docs/architecture/` |
| 開発・検証・依存管理の手順 | `docs/guides/` |
| 実装から抽出した技術者向け案内 | `docs/codebase/` |
| 未確定の実装計画 | `docs/plans/` |

`docs/plans/`は現行仕様ではない。完了した変更の恒久情報は、該当する`spec.md`、`reference/`、`architecture/`、`guides/`へ移す。

## 3. MkDocs

`mkdocs.yml`は`docs/`全体を公開し、`site/docs/`へ出力する。

```bash
# ローカル表示
uv run --group dev mkdocs serve

# リンク・ナビゲーションを含む厳格な検証
uv run --group dev mkdocs build --strict
```

公開対象外の一時ファイル、秘密情報、生成物を`docs/`へ置かない。`site/`は生成物であり、編集しない。

## 4. 更新時の確認

1. 変更したコード、設定、テストを根拠として記述する。
2. 目標・計画・現行実装を同じ段落で混ぜない。
3. 新しい恒久文書を追加したら`mkdocs.yml`のナビゲーションへ追加する。
4. `mkdocs build --strict`を実行する。
5. `docs/codebase/`を更新した場合は、`dashboard/`の事実も同じ根拠で更新し、バリデーターを実行する。

## 5. 根拠

* `mkdocs.yml`
* `docs/reference/spec-governance.md`
* `README.md`
