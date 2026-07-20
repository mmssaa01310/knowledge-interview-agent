from ai_interviewer_voice.clients.interview_api import InterviewApiClient
from ai_interviewer_voice.config import settings
from ai_interviewer_voice.runtimes.base import RealtimeVoiceRuntime
from ai_interviewer_voice.runtimes.fake_runtime import FakeRuntime
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import TranscribePollyRuntime
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
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")
    raise ValueError(f"Unsupported voice runtime provider: {provider}")
