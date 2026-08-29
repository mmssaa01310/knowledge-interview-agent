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
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "memory://local")
    sqs_document_queue_url: str = os.getenv(
        "SQS_DOCUMENT_QUEUE_URL", "memory://document-ingestion"
    )
    bedrock_enabled: bool = os.getenv("BEDROCK_ENABLED", "true").lower() == "true"
    strands_interview_agent_enabled: bool = (
        os.getenv("STRANDS_INTERVIEW_AGENT_ENABLED", "false").lower() == "true"
    )
    bedrock_aws_region: str = os.getenv("BEDROCK_AWS_REGION", "ap-northeast-1")
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID",
        "apac.amazon.nova-pro-v1:0",
    )
    bedrock_fallback_model_id: str = os.getenv("BEDROCK_FALLBACK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
    bedrock_max_tokens: int = int(os.getenv("BEDROCK_MAX_TOKENS", "2400"))
    bedrock_temperature: float = float(os.getenv("BEDROCK_TEMPERATURE", "0.2"))
    # The deployment templates enable this path. The code-level fallback stays
    # disabled when the environment variable is omitted for compatibility with
    # callers that construct the API without deployment configuration.
    structured_interview_enabled: bool = (
        os.getenv("STRUCTURED_INTERVIEW_ENABLED", "false").lower() == "true"
    )
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
    voice_answer_evaluation_deadline_seconds: float = float(
        os.getenv("VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS", "2.0")
    )
    voice_bedrock_model_id: str = os.getenv(
        "VOICE_BEDROCK_MODEL_ID",
        os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-pro-v1:0"),
    )
    voice_bedrock_temperature: float = float(
        os.getenv("VOICE_BEDROCK_TEMPERATURE", "0.0")
    )
    voice_bedrock_max_tokens: int = int(os.getenv("VOICE_BEDROCK_MAX_TOKENS", "600"))
    voice_bedrock_connect_timeout_seconds: float = float(
        os.getenv("VOICE_BEDROCK_CONNECT_TIMEOUT_SECONDS", "0.5")
    )
    voice_bedrock_read_timeout_seconds: float = float(
        os.getenv("VOICE_BEDROCK_READ_TIMEOUT_SECONDS", "1.8")
    )
    voice_bedrock_warmup_enabled: bool = (
        os.getenv("VOICE_BEDROCK_WARMUP_ENABLED", "true").lower() == "true"
    )
    internal_api_token: str = os.getenv("INTERNAL_API_TOKEN", "dev-internal-token")


settings = Settings()
