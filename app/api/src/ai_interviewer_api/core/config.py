from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "AI Interviewer API")
    app_env: str = os.getenv("APP_ENV", "local")
    cognito_user_pool_id: str = os.getenv("COGNITO_USER_POOL_ID", "local-placeholder")
    cognito_region: str = os.getenv("COGNITO_REGION", "ap-northeast-1")
    cognito_app_client_id: str = os.getenv("COGNITO_APP_CLIENT_ID", "local-placeholder")
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "memory://local")
    sqs_document_queue_url: str = os.getenv(
        "SQS_DOCUMENT_QUEUE_URL", "memory://document-ingestion"
    )
    bedrock_enabled: bool = os.getenv("BEDROCK_ENABLED", "true").lower() == "true"
    bedrock_aws_region: str = os.getenv("BEDROCK_AWS_REGION", "ap-northeast-1")
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID",
        "apac.amazon.nova-pro-v1:0",
    )
    bedrock_fallback_model_id: str = os.getenv("BEDROCK_FALLBACK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
    bedrock_max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "2400"))
    bedrock_temperature: float = float(os.getenv("BEDROCK_TEMPERATURE", "0.2"))


settings = Settings()
