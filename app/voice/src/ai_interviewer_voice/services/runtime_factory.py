from ai_interviewer_voice.clients.interview_api import InterviewApiClient
from ai_interviewer_voice.config import settings
from ai_interviewer_voice.runtimes.base import RealtimeVoiceRuntime
from ai_interviewer_voice.runtimes.fake_runtime import FakeRuntime
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import (
    TranscribePollyRuntime,
)
from ai_interviewer_voice.services.interview_bridge import InterviewBridge


def create_runtime(provider: str) -> RealtimeVoiceRuntime:
    normalized = provider.strip().lower()
    if normalized == "fake":
        if settings.app_env not in {"local", "test"}:
            raise ValueError("fake runtime is only available in local/test environments")
        return FakeRuntime()
    if normalized == "nova_sonic":
        bridge = InterviewBridge(
            InterviewApiClient(
                settings.api_base_url,
                settings.internal_api_token,
            ),
            turn_save_timeout_seconds=settings.interview_turn_save_timeout_seconds,
            turn_process_timeout_seconds=settings.interview_turn_process_timeout_seconds,
        )
        return NovaSonicRuntime(
            config=NovaSonicRuntimeConfig(
                aws_region=settings.aws_region,
                model_id=settings.nova_sonic_model_id,
                voice_id=settings.nova_sonic_voice_id,
                endpointing_sensitivity=settings.nova_sonic_endpointing_sensitivity,
                invoke_timeout_seconds=settings.nova_sonic_invoke_timeout_seconds,
                await_output_timeout_seconds=settings.nova_sonic_await_output_timeout_seconds,
                system_prompt=settings.nova_sonic_system_prompt,
                enable_forced_tool_use=True,
                forced_tool_result_delay_ms=settings.forced_tool_result_delay_ms,
                normal_turn_tool_result_target_ms=settings.normal_turn_tool_result_target_ms,
                normal_turn_tool_result_budget_ms=settings.normal_turn_tool_result_budget_ms,
                interview_timeout_reply_text=settings.interview_timeout_reply_text,
                interview_error_reply_text=settings.interview_error_reply_text,
                interview_unauthorized_reply_text=settings.interview_unauthorized_reply_text,
            ),
            interview_bridge=bridge,
        )
    if normalized == "transcribe_polly":
        bridge = InterviewBridge(
            InterviewApiClient(
                settings.api_base_url,
                settings.internal_api_token,
            ),
            turn_save_timeout_seconds=settings.interview_turn_save_timeout_seconds,
            turn_process_timeout_seconds=settings.interview_turn_process_timeout_seconds,
        )
        return TranscribePollyRuntime(
            config=TranscribePollyRuntimeConfig(
                aws_region=settings.aws_region,
                language_code=settings.transcribe_language_code,
                transcribe_chunk_ms=settings.transcribe_chunk_ms,
                partial_results_stability=settings.transcribe_partial_results_stability,
                transcribe_reconnect_attempts=settings.transcribe_reconnect_attempts,
                reconnect_audio_buffer_ms=settings.transcribe_reconnect_audio_buffer_ms,
                vad_rms_threshold=settings.transcribe_vad_rms_threshold,
                polly_voice_id=settings.polly_voice_id,
                polly_engine=settings.polly_engine,
                polly_max_parallel_requests=settings.polly_max_parallel_requests,
                backchannel_enabled=settings.voice_enable_backchannels,
            ),
            interview_bridge=bridge,
        )
    raise ValueError(f"Unsupported voice runtime provider: {provider}")
