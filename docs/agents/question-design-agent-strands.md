# 質問設計エージェント

## 1. 目的

質問設計エージェントは、ナレッジ情報と利用者の依頼から、熟練者に聞く質問項目の候補を作る。質問設計、構造化出力の検証、保存・承認を分離する。

## 2. 責務

- Backendが対象テナント・Knowledgeの範囲内で参考情報を検索する。
- OpenAI Responses APIへ検索済み情報を渡し、Structured Outputsで質問項目候補を生成・検証する。
- Repositoryへの保存と正式な質問項目への反映はBackendが行う。
- 質問候補は管理者の承認前に正式な質問項目へ追加しない。

## 3. モデルとキー

質問設計では`gpt-6-luna`または`gpt-6.1-terra`を使用する。Knowledgeの`defaultModelId`で選択し、未設定時は`QUESTION_DESIGN_MODEL_ID`（既定`gpt-6-luna`）を使う。候補生成とValidatorには同じモデルを使い、自動でモデルを切り替えない。

アプリケーション管理者が設定画面に登録したOpenAI Project APIキーをBackendから取得する。キーはAES-GCMで暗号化して保存し、Browserやログへ出力しない。

## 4. Structured Outputs

候補生成とValidatorはそれぞれResponses APIへリクエストし、Pydantic Schemaで応答を検証する。Mermaidコード、React Flow座標、DB更新はLLMに生成させない。Backendはテナント範囲、文書の取込状態、候補数と形式を保証する。

## 5. 検証とエラー

通常のテストでは実OpenAI APIを呼び出さず、Providerを置き換える。API接続失敗は記録せずにAPIエラーとして返し、別Providerや別モデルへの暗黙のfallbackを行わない。
