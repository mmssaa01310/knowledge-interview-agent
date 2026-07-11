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
AWS_REGION=ap-northeast-1
AWS_DEFAULT_REGION=ap-northeast-1
```

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
