# Issue tracker: GitHub

このリポジトリのIssueはGitHub Issuesで管理し、操作には`gh` CLIを使います。

## 基本操作

- 作成: `gh issue create --title "..." --body-file <file>`
- 参照: `gh issue view <number> --json number,title,body,labels,comments`
- 一覧: `gh issue list --state open`。必要に応じてラベルや状態で絞り込みます。
- コメント: `gh issue comment <number> --body "..."`
- ラベル変更: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`

## 親子関係と依存関係

GitHubのサブIssueとIssue依存関係を利用します。利用中の`gh`が対応していない場合は、本文に`Part of #<number>`または`Blocked by: #<number>`を記載します。

## 登録先の確認

Issueを初めて作成する前に`gh repo view --json nameWithOwner`で対象を確認します。対象は現在の作業リポジトリのリモート設定から判断します。登録先が依頼対象と一致しない場合は、作成前に利用者へ確認します。

## ラベル

`to-tickets`が`ready-for-agent`を付ける場合、GitHub上にそのラベルがあることを確認します。存在しない場合は、作成前に利用者へ確認し、無断では作成しません。

## PRの扱い

PRはIssueの受付窓口としては扱いません。PRのレビューや作成はGitHubのPRとして別に扱います。

## Issue trackerへ登録する指示

スキルから「issue trackerへ公開」と指示された場合は、GitHub Issueを作成します。
