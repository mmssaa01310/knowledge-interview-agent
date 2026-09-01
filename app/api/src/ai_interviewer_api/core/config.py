from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "KIKIORI API")
    app_env: str = os.getenv("APP_ENV", "local")
    dev_auto_seed_voice_demo: bool = (
        os.getenv("DEV_AUTO_SEED_VOICE_DEMO", "false").lower() == "true"
    )
    dev_auto_seed_maintenance_demo: bool = (
        os.getenv("DEV_AUTO_SEED_MAINTENANCE_DEMO", "false").lower() == "true"
    )
    dev_auto_seed_system_requirement_demo: bool = (
        os.getenv("DEV_AUTO_SEED_SYSTEM_REQUIREMENT_DEMO", "false").lower() == "true"
    )
    cognito_user_pool_id: str = os.getenv("COGNITO_USER_POOL_ID", "local-placeholder")
    cognito_region: str = os.getenv("COGNITO_REGION", "ap-northeast-1")
    cognito_app_client_id: str = os.getenv("COGNITO_APP_CLIENT_ID", "local-placeholder")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://kikiori:kikiori@localhost:5432/kikiori",
    )
    document_knowledge_backend: str = os.getenv(
        "DOCUMENT_KNOWLEDGE_BACKEND",
        "postgres",
    )
    elasticsearch_cloud_id: str = os.getenv("ELASTICSEARCH_CLOUD_ID", "")
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "")
    elasticsearch_api_key: str = os.getenv("ELASTICSEARCH_API_KEY", "")
    elasticsearch_username: str = os.getenv("ELASTICSEARCH_USERNAME", "")
    elasticsearch_password: str = os.getenv("ELASTICSEARCH_PASSWORD", "")
    elasticsearch_verify_certs: bool = (
        os.getenv("ELASTICSEARCH_VERIFY_CERTS", "true").lower() == "true"
    )
    elasticsearch_request_timeout_seconds: float = float(
        os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT_SECONDS", "10")
    )
    elasticsearch_document_index: str = os.getenv(
        "ELASTICSEARCH_DOCUMENT_INDEX",
        "kikiori-documents-v1",
    )
    elasticsearch_document_chunk_index: str = os.getenv(
        "ELASTICSEARCH_DOCUMENT_CHUNK_INDEX",
        "kikiori-document-chunks-v1",
    )
    document_max_upload_bytes: int = int(
        os.getenv("DOCUMENT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
    )
    document_chunk_size_chars: int = int(
        os.getenv("DOCUMENT_CHUNK_SIZE_CHARS", "1200")
    )
    document_chunk_overlap_chars: int = int(
        os.getenv("DOCUMENT_CHUNK_OVERLAP_CHARS", "150")
    )
    sqs_document_queue_url: str = os.getenv(
        "SQS_DOCUMENT_QUEUE_URL", "memory://document-ingestion"
    )
    bedrock_enabled: bool = os.getenv("BEDROCK_ENABLED", "true").lower() == "true"
    bedrock_aws_region: str = os.getenv("BEDROCK_AWS_REGION", "ap-northeast-1")
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID",
        "apac.amazon.nova-pro-v1:0",
    )
    bedrock_max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "2400"))
    bedrock_temperature: float = float(os.getenv("BEDROCK_TEMPERATURE", "0.2"))
    structured_interview_model_id: str = os.getenv(
        "STRUCTURED_INTERVIEW_MODEL_ID",
        "global.openai.gpt-5.6-luna",
    )
    structured_interview_connect_timeout_seconds: float = float(
        os.getenv("STRUCTURED_INTERVIEW_CONNECT_TIMEOUT_SECONDS", "5")
    )
    structured_interview_read_timeout_seconds: float = float(
        os.getenv("STRUCTURED_INTERVIEW_READ_TIMEOUT_SECONDS", "120")
    )
    structured_interview_reasoning_effort: str = os.getenv(
        "STRUCTURED_INTERVIEW_REASONING_EFFORT",
        "low",
    )
    structured_interview_medium_reasoning_effort: str = os.getenv(
        "STRUCTURED_INTERVIEW_MEDIUM_REASONING_EFFORT",
        "medium",
    )
    structured_interview_max_output_tokens: int = int(
        os.getenv("STRUCTURED_INTERVIEW_MAX_OUTPUT_TOKENS", "6000")
    )
    structured_interview_question_max_output_tokens: int = int(
        os.getenv("STRUCTURED_INTERVIEW_QUESTION_MAX_OUTPUT_TOKENS", "600")
    )
    question_design_model_id: str = os.getenv(
        "QUESTION_DESIGN_MODEL_ID",
        "global.openai.gpt-5.6-luna",
    )
    question_design_reasoning_effort: str = os.getenv(
        "QUESTION_DESIGN_REASONING_EFFORT",
        "low",
    )
    question_design_max_output_tokens: int = int(
        os.getenv("QUESTION_DESIGN_MAX_OUTPUT_TOKENS", "6000")
    )
    question_design_temperature: float = float(
        os.getenv("QUESTION_DESIGN_TEMPERATURE", "0.0")
    )
    internal_api_token: str = os.getenv("INTERNAL_API_TOKEN", "dev-internal-token")


settings = Settings()
