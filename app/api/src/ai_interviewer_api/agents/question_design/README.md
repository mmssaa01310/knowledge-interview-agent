# Question Design Agent

質問設計エージェントは、ユーザーの目的から熟練者に聞くべき質問項目を設計する。

現在の `field-suggestions` はこの責務に属する。
当面は既存の `services/field_suggestions.py` と `services/prompts/field_fill/` を維持し、将来的に小さく移行する。

このエージェントは、承認前の候補生成までを担当する。
正式なヒアリング項目として保存するには、ユーザーの承認操作を必須とする。
