# Common Agent Infrastructure

エージェント共通の Bedrock 呼び出し、prompt loader、JSON parser、contract retry、observability、read-only tools の置き場とする。

現時点では実装を置かず、責務の受け皿だけを用意する。
tool は read-only から開始し、自律的なDB更新を行わない。
