# aws-local-development.md

## 1. 前提

ローカル開発では AWS CLI profile を使う。

profile は access key / secret key、assume role、SSO のいずれでもよい。

このリポジトリでは SSO を必須にしない。

アクセスキーを `.env` やコードへ直書きしない。

Docker コンテナからはホストの `~/.aws` を read-only で参照する。

Bedrock 呼び出しに必要な IAM 権限は次を含める。

* `bedrock:InvokeModel`
* `bedrock:InvokeModelWithResponseStream`
* `bedrock:Converse`
* `bedrock:ConverseStream`

対象リージョンで Bedrock のモデルアクセスが有効化されていることを前提とする。

## 2. Docker から Bedrock を呼ぶ設定

`infra/docker-compose.yml` の `api` サービスでは、以下を渡す。

* `AWS_PROFILE`
* `AWS_REGION`
* `AWS_DEFAULT_REGION`
* `BEDROCK_AWS_REGION`
* `AWS_SDK_LOAD_CONFIG=1`

また、ホストの `~/.aws` を `/root/.aws:ro` でマウントする。

これにより、コンテナ内の boto3 / AWS SDK がホストの profile 設定を参照できる。

## 3. ローカル確認手順

1. ホスト側で AWS CLI profile を準備する。
2. `.env` かシェル環境で `AWS_PROFILE` とリージョンを設定する。
3. `docker compose -f infra/docker-compose.yml up --build` を実行する。
4. API から Bedrock を使う画面やエンドポイントを確認する。

### Bedrock 環境変数の例

```env
BEDROCK_MODEL_ID=apac.amazon.nova-pro-v1:0
BEDROCK_AWS_REGION=ap-northeast-1
VOICE_BEDROCK_MODEL_ID=apac.amazon.nova-pro-v1:0
VOICE_BEDROCK_TEMPERATURE=0.0
VOICE_BEDROCK_MAX_TOKENS=600
VOICE_BEDROCK_WARMUP_ENABLED=true
AWS_REGION=ap-northeast-1
AWS_DEFAULT_REGION=ap-northeast-1
STRUCTURED_INTERVIEW_ENABLED=true
STRUCTURED_INTERVIEW_MODEL_ID=global.openai.gpt-5.6-terra
QUESTION_DESIGN_MODEL_ID=global.openai.gpt-5.6-terra
STRUCTURED_INTERVIEW_REASONING_EFFORT=low
STRUCTURED_INTERVIEW_MEDIUM_REASONING_EFFORT=medium
STRUCTURED_INTERVIEW_MAX_OUTPUT_TOKENS=6000
STRUCTURED_INTERVIEW_QUESTION_MAX_OUTPUT_TOKENS=600
STRUCTURED_INTERVIEW_CONNECT_TIMEOUT_SECONDS=5
STRUCTURED_INTERVIEW_READ_TIMEOUT_SECONDS=120
```

構造化インタビューを有効にすると、APIは`BEDROCK_AWS_REGION`の`bedrock-runtime`へAWS SigV4で接続し、`global.openai.gpt-5.6-terra`またはナレッジ設定で選択された`global.openai.gpt-5.6-luna`を呼び出す。OpenAI APIキーは設定しない。Global profileは対応する商用AWSリージョンへルーティングされるため、データ処理リージョンを限定する要件がある環境では使用しない。

質問項目設計も同じGlobal profileを使用する。ナレッジ設定の「質問項目の設計モデル」でTerraまたはLunaを選択し、未設定時は`QUESTION_DESIGN_MODEL_ID`の値を使用する。質問項目設計にAmazon Novaまたは画像生成モデルを使用してはならない。

ユーザーが提示したGlobal profileのARNは、Terraが`arn:aws:bedrock:us-east-1:755974828484:inference-profile/global.openai.gpt-5.6-terra`、Lunaが`arn:aws:bedrock:us-east-1:755974828484:inference-profile/global.openai.gpt-5.6-luna`である。ARNを直接設定する場合は`BEDROCK_AWS_REGION=us-east-1`とし、通常はリージョンに依存しないprofile IDを設定する。

`VOICE_BEDROCK_MODEL_ID`は、`STRUCTURED_INTERVIEW_ENABLED=false`で旧経路を使う場合のリアルタイム音声回答評価にだけ適用する。標準の構造化インタビューでは、回答解析と次の質問生成に`STRUCTURED_INTERVIEW_MODEL_ID`を使用し、音声入出力にはTranscribeとPollyを使用する。旧経路を比較する場合は、以下のモデルのいずれかを設定し、APIコンテナを再作成する。

```env
# Nova Pro
VOICE_BEDROCK_MODEL_ID=apac.amazon.nova-pro-v1:0

# Claude Sonnet 4.5 (Japan cross-region inference profile)
VOICE_BEDROCK_MODEL_ID=jp.anthropic.claude-sonnet-4-5-20250929-v1:0
VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS=6.0
VOICE_BEDROCK_READ_TIMEOUT_SECONDS=5.5

# Qwen3 Next 80B A3B (Tokyo region)
VOICE_BEDROCK_MODEL_ID=qwen.qwen3-next-80b-a3b
```

```bash
docker compose -f infra/docker-compose.yml up -d --force-recreate api
```

Sonnet 4.5の上記timeout値は旧経路の品質比較用であり、構造化インタビューの標準設定には使用しない。旧経路の標準設定へ戻す場合は、`STRUCTURED_INTERVIEW_ENABLED=false`を明示し、モデルIDをNova Proへ戻し、deadlineを`2.0`、read timeoutを`1.8`に戻す。

### profile の例

`~/.aws/credentials`

```ini
[ai-interviewer-dev]
aws_access_key_id = AKIAxxxxxxxxxxxx
aws_secret_access_key = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`~/.aws/config`

```ini
[profile ai-interviewer-dev]
region = ap-northeast-1
output = json
```

### SSO profile を使う場合

SSO profile を使う場合のみ、事前に `aws sso login --profile <profile-name>` を実行する。

## 4. エラー切り分け

### EndpointConnectionError

Bedrock Runtime へ到達できていない。

確認する点:

* リージョンが正しいか
* `AWS_PROFILE` が存在するか
* profile の認証情報や assume role 設定が有効か
* SSO profile を使う場合は `aws sso login` 済みか
* コンテナから `~/.aws` が見えているか
* 対象リージョンに Bedrock のモデルアクセスが有効か

### AccessDeniedException

認証は通っているが、権限不足かモデルアクセス不足。

確認する点:

* IAM に `bedrock:InvokeModel`
* IAM に `bedrock:InvokeModelWithResponseStream`
* IAM に `bedrock:Converse`
* IAM に `bedrock:ConverseStream`
* Bedrock のモデルアクセスが許可されているか
* Global inference profileを使用する場合、inference profile、`project/default`、呼び出し元リージョンのfoundation model、Global foundation modelへの`bedrock:InvokeModel`が許可されているか

### ValidationException

modelId に指定した値が、その環境では無効。

確認する点:

* Bedrock の通常モデルIDと inference profile ID を混同していないか
* 対象リージョンで cross-region inference 用の inference profile ID を使う必要がないか
* AWS コンソールの Bedrock `Model access`、`Cross-region inference`、`Inference profiles` で利用可能なIDを確認したか

この環境では、Nova Pro 呼び出しに `apac.amazon.nova-pro-v1:0` は通り、`global.amazon.nova-pro-v1:0` は `ValidationException` になった。

### JSON parse failure

Bedrock から返った内容が JSON として読めていない。

確認する点:

* system prompt が JSON 形式の返答を明示しているか
* `fields` と `reply` を含む形で返しているか
* JSON repair の再試行が必要な壊れ方か

## 5. 秘密情報の扱い

アクセスキー、シークレットキー、セッション情報を `.env` やコードに直書きしない。

コミットするのは設定例だけにし、秘密情報そのものは含めない。
