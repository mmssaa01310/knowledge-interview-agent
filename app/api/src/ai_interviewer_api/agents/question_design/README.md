# Question Design Agent

質問設計エージェントは、ユーザーの目的から熟練者に聞くべき質問項目を設計する。

現在の `field-suggestions` はこの責務に属する。
router 互換のため `services/field_suggestions.py` は薄いラッパーとして残し、実際の生成処理は Strands question_design agent に寄せる。

このエージェントは、承認前の候補生成までを担当する。
正式なヒアリング項目として保存するには、ユーザーの承認操作を必須とする。
