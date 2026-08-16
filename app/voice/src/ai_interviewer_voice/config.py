import os
from dataclasses import dataclass

from ai_interviewer_voice.runtimes.nova_sonic.config import DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "AI Interviewer Voice")
    app_env: str = os.getenv("APP_ENV", "local")
    api_base_url: str = os.getenv("VOICE_API_BASE_URL", "http://127.0.0.1:8000")
    internal_api_token: str = os.getenv("INTERNAL_API_TOKEN", "dev-internal-token")
    runtime_provider: str = os.getenv("VOICE_RUNTIME_PROVIDER", "transcribe_polly")
    aws_region: str = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1"))
    nova_sonic_model_id: str = os.getenv("NOVA_SONIC_MODEL_ID", "amazon.nova-2-sonic-v1:0")
    nova_sonic_voice_id: str = os.getenv("NOVA_SONIC_VOICE_ID", "matthew")
    nova_sonic_endpointing_sensitivity: str = os.getenv("NOVA_SONIC_ENDPOINTING_SENSITIVITY", "MEDIUM")
    nova_sonic_invoke_timeout_seconds: float = float(os.getenv("NOVA_SONIC_INVOKE_TIMEOUT_SECONDS", "10"))
    nova_sonic_await_output_timeout_seconds: float = float(
        os.getenv("NOVA_SONIC_AWAIT_OUTPUT_TIMEOUT_SECONDS", "10")
    )
    nova_sonic_system_prompt: str = os.getenv(
        "NOVA_SONIC_SYSTEM_PROMPT",
        DEFAULT_SYSTEM_PROMPT,
    )
    transcribe_language_code: str = os.getenv("TRANSCRIBE_LANGUAGE_CODE", "ja-JP")
    transcribe_partial_results_stability: str = os.getenv(
        "TRANSCRIBE_PARTIAL_RESULTS_STABILITY", "medium"
    )
    transcribe_chunk_ms: int = int(os.getenv("TRANSCRIBE_AUDIO_CHUNK_MS", "100"))
    transcribe_reconnect_attempts: int = int(
        os.getenv("TRANSCRIBE_RECONNECT_ATTEMPTS", "2")
    )
    transcribe_reconnect_audio_buffer_ms: int = int(
        os.getenv("TRANSCRIBE_RECONNECT_AUDIO_BUFFER_MS", "3000")
    )
    transcribe_vad_rms_threshold: int = int(
        os.getenv("TRANSCRIBE_VAD_RMS_THRESHOLD", "600")
    )
    polly_voice_id: str = os.getenv("POLLY_VOICE_ID", "Kazuha")
    polly_engine: str = os.getenv("POLLY_ENGINE", "neural")
    polly_max_parallel_requests: int = int(
        os.getenv("POLLY_MAX_PARALLEL_REQUESTS", "2")
    )
    interview_turn_save_timeout_seconds: float = float(os.getenv("VOICE_TURN_SAVE_TIMEOUT_SECONDS", "5"))
    interview_turn_process_timeout_seconds: float = float(os.getenv("VOICE_TURN_PROCESS_TIMEOUT_SECONDS", "5"))
    interview_timeout_reply_text: str = os.getenv(
        "VOICE_INTERVIEW_TIMEOUT_REPLY_TEXT",
        "処理に時間がかかっています。もう一度お願いします。",
    )
    interview_error_reply_text: str = os.getenv(
        "VOICE_INTERVIEW_ERROR_REPLY_TEXT",
        "処理に失敗しました。もう一度お願いします。",
    )
    interview_unauthorized_reply_text: str = os.getenv(
        "VOICE_INTERVIEW_UNAUTHORIZED_REPLY_TEXT",
        "認証を確認できませんでした。セッションを終了します。",
    )
    forced_tool_result_delay_ms: int = int(os.getenv("VOICE_FORCED_TOOL_RESULT_DELAY_MS", "0"))
    normal_turn_tool_result_target_ms: int = int(os.getenv("VOICE_NORMAL_TURN_TOOL_RESULT_TARGET_MS", "300"))
    normal_turn_tool_result_budget_ms: int = int(os.getenv("VOICE_NORMAL_TURN_TOOL_RESULT_BUDGET_MS", "400"))
    webrtc_ice_gathering_timeout_seconds: float = float(
        os.getenv("VOICE_WEBRTC_ICE_GATHERING_TIMEOUT_SECONDS", "2")
    )
    webrtc_peer_disconnected_grace_seconds: float = float(
        os.getenv("VOICE_WEBRTC_PEER_DISCONNECTED_GRACE_SECONDS", "5")
    )
    webrtc_completion_wait_timeout_seconds: float = float(
        os.getenv("VOICE_WEBRTC_COMPLETION_WAIT_TIMEOUT_SECONDS", "5")
    )
    webrtc_audio_input_queue_max_frames: int = int(
        os.getenv("VOICE_WEBRTC_AUDIO_INPUT_QUEUE_MAX_FRAMES", "12")
    )
    webrtc_playback_buffer_target_ms: float = float(
        os.getenv("VOICE_WEBRTC_PLAYBACK_BUFFER_TARGET_MS", "100")
    )
    webrtc_playback_buffer_retention_max_ms: float = float(
        os.getenv("VOICE_WEBRTC_PLAYBACK_BUFFER_RETENTION_MAX_MS", "60000")
    )
    webrtc_playback_preroll_ms: float = float(
        os.getenv("VOICE_WEBRTC_PLAYBACK_PREROLL_MS", "80")
    )
    webrtc_playback_short_underrun_ms: float = float(
        os.getenv("VOICE_WEBRTC_PLAYBACK_SHORT_UNDERRUN_MS", "40")
    )
    kvs_turn_channel_arn: str | None = os.getenv("VOICE_KVS_TURN_CHANNEL_ARN")
    kvs_turn_cache_ttl_seconds: float = float(os.getenv("VOICE_KVS_TURN_CACHE_TTL_SECONDS", "240"))
    host: str = os.getenv("VOICE_HOST", "0.0.0.0")
    port: int = int(os.getenv("VOICE_PORT", "8010"))


settings = Settings()
