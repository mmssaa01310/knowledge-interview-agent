# Question Design Agent

質問設計エージェントは、ユーザーの目的から熟練者に聞くべき質問項目を設計する。

現在の `field-suggestions` はこの責務に属する。
router 互換のため `services/field_suggestions.py` は薄いラッパーとして残し、実際の生成処理はBedrockのOpenAI互換Responses APIとStructured Outputsで実行する。

Backendは生成前に、同じナレッジに属する既存質問項目、承認済み記録、承認済み提案、インデックス済み文書・チャンクを検索し、`retrieved_knowledge`としてLLMへ渡す。LLMはDBへ直接アクセスせず、検索結果を追加の参考情報として扱う。

このエージェントは、承認前の候補生成までを担当する。
正式な質問項目として保存するには、ユーザーの承認操作を必須とする。
