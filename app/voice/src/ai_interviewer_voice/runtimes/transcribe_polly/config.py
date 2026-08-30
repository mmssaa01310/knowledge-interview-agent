from dataclasses import dataclass


@dataclass(frozen=True)
class TranscribePollyRuntimeConfig:
    provider_name: str = "transcribe_polly"
    aws_region: str = "ap-northeast-1"
    interview_locale: str = "ja-JP"
    language_code: str = "ja-JP"
    input_sample_rate_hz: int = 16000
    transcribe_chunk_ms: int = 100
    partial_results_stability: str = "medium"
    transcribe_reconnect_attempts: int = 2
    reconnect_audio_buffer_ms: int = 3000
    vad_rms_threshold: int = 600
    vad_soft_endpoint_ms: int = 350
    listen_ack_silence_ms: int = 500
    normal_endpoint_ms: int = 600
    hard_endpoint_ms: int = 1000
    final_result_wait_ms: int = 300
    final_result_settle_ms: int = 150
    long_form_speech_ms: int = 2500
    long_form_endpoint_ms: int = 850
    listen_ack_min_speech_ms: int = 800
    listen_ack_min_stable_chars: int = 5
    backchannel_cooldown_ms: int = 3000
    backchannel_enabled: bool = False
    processing_ack_delay_ms: int = 1300
    long_processing_notice_ms: int = 3000
    barge_in_voice_ms: int = 120
    polly_voice_id: str = "Kazuha"
    polly_language_code: str = "ja-JP"
    polly_engine: str = "neural"
    polly_sample_rate_hz: int = 16000
    polly_max_parallel_requests: int = 2
    polly_retry_attempts: int = 1
    polly_retry_base_delay_ms: int = 150
    first_chunk_min_chars: int = 10
    first_chunk_max_chars: int = 30
    following_chunk_min_chars: int = 20
    following_chunk_max_chars: int = 80
    listen_ack_text: str = "はい。"
    processing_ack_text: str = "回答を確認しています。"
    long_processing_text: str = "確認に少し時間がかかっています。"
