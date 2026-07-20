from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.clients.interview_api import InterviewApiClient
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.schemas.events import AssistantAudioChunk, RuntimeError, RuntimeReady
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext
from ai_interviewer_voice.services.interview_bridge import InterviewBridge
from smoke_nova_sonic_helpers import (
    DEFAULT_POLLY_TEXT,
    PcmAudioFixture,
    chunk_duration_seconds,
    iter_pcm_chunks,
    load_or_generate_pcm,
    save_pcm_as_wav,
    trailing_silence_chunks,
)


TEXT_EN_SYSTEM_PROMPT = "You are a concise voice assistant. Respond in English using one short sentence."
INTERVIEW_API_SYSTEM_PROMPT = (
    "You are a voice assistant for a structured interview. "
    "Speak in Japanese when you receive Japanese text instructions."
)
PROMPT_SUPPRESSION_SYSTEM_PROMPT = (
    "Do not answer the user's spoken input directly.\n"
    "After the user finishes speaking, wait silently.\n"
    "Only speak when you receive a text instruction.\n"
    "When a text instruction is received, speak only the requested content\n"
    "without adding explanations or new questions."
)
FORCED_TOOL_SYSTEM_PROMPT_SUFFIX = (
    "\n\nFor every completed user turn, call the process_interview_turn tool before speaking.\n"
    "Do not respond to the user before receiving the tool result.\n"
    "After receiving the tool result, speak only the value of reply_text.\n"
    "Do not add explanations, acknowledgements, introductions, or additional questions.\n"
    "Do not rephrase reply_text."
)
TEXT_EN_USER_TEXT = "Say exactly: Connection test successful."
TEXT_JA_USER_TEXT = "「接続テスト成功」と日本語でそのまま話してください。"
APPROVED_REPLY_TEXT = "Thank you. Please tell me when this problem usually occurs."
MINIMAL_TOOL_REPLY_TEXT = "Connection test successful."
ASSISTANT_PCM_PATH = "/tmp/nova-sonic-smoke-output.pcm"
ASSISTANT_WAV_PATH = "/tmp/nova-sonic-smoke-output.wav"


def _is_smoke_enabled() -> bool:
    return os.getenv("RUN_NOVA_SONIC_OPEN_STREAM") == "1" or os.getenv("RUN_NOVA_SONIC_SMOKE") == "1"


def _resolve_smoke_mode() -> str:
    return os.getenv("NOVA_SONIC_SMOKE_MODE", "text_en")


def _resolve_text_payload(mode: str) -> tuple[str, str]:
    if mode.startswith("text_ja"):
        return TEXT_EN_SYSTEM_PROMPT, TEXT_JA_USER_TEXT
    return TEXT_EN_SYSTEM_PROMPT, TEXT_EN_USER_TEXT


def _resolve_system_prompt(mode: str) -> str:
    if mode == "interview_api_authorized_generation":
        return INTERVIEW_API_SYSTEM_PROMPT + FORCED_TOOL_SYSTEM_PROMPT_SUFFIX
    if mode.startswith("forced_tool_") or mode == "interview_api_authorized_generation":
        return TEXT_EN_SYSTEM_PROMPT + FORCED_TOOL_SYSTEM_PROMPT_SUFFIX
    if mode == "authorized_generation_prompt_suppression":
        return PROMPT_SUPPRESSION_SYSTEM_PROMPT
    return TEXT_EN_SYSTEM_PROMPT


async def _consume_events(runtime: NovaSonicRuntime) -> list[object]:
    events: list[object] = []
    async for event in runtime.events():
        events.append(event)
    return events


async def _stream_pcm_realtime(runtime: NovaSonicRuntime, fixture: PcmAudioFixture) -> int:
    sent = 0
    for chunk in iter_pcm_chunks(fixture.pcm):
        await runtime.push_audio(chunk)
        sent += 1
        await asyncio.sleep(chunk_duration_seconds(chunk))
    return sent


async def _stream_trailing_silence(runtime: NovaSonicRuntime) -> int:
    sent = 0
    for chunk in trailing_silence_chunks():
        await runtime.push_audio(chunk)
        sent += 1
        await asyncio.sleep(chunk_duration_seconds(chunk))
    return sent


async def _stream_silence_until_stopped(
    runtime: NovaSonicRuntime,
    stop_event: asyncio.Event,
) -> int:
    sent = 0
    silence_chunk = bytes(2048)
    while not stop_event.is_set():
        await runtime.push_audio(silence_chunk)
        runtime.mark_completion_wait_silence_frame()
        sent += 1
        await asyncio.sleep(chunk_duration_seconds(silence_chunk))
    return sent


def _assistant_audio_from_events(events: list[object]) -> bytes:
    audio = bytearray()
    for event in events:
        if isinstance(event, AssistantAudioChunk):
            audio.extend(event.pcm)
    return bytes(audio)


async def main() -> int:
    if not _is_smoke_enabled():
        print("Set RUN_NOVA_SONIC_OPEN_STREAM=1 to run the Nova Sonic smoke check.")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore.credentials").setLevel(logging.WARNING)
    logging.getLogger("ai_interviewer_voice.runtimes.nova_sonic.runtime").setLevel(logging.INFO)

    smoke_mode = _resolve_smoke_mode()
    _, user_text = _resolve_text_payload(smoke_mode)
    system_prompt = _resolve_system_prompt(smoke_mode)
    pcm_path = os.getenv("NOVA_SONIC_SMOKE_PCM_PATH")
    voice_session_id = os.getenv("NOVA_SONIC_SMOKE_VOICE_SESSION_ID", "smoke-voice-session")
    record_id = os.getenv("NOVA_SONIC_SMOKE_RECORD_ID", "smoke-record")
    audio_fixture: PcmAudioFixture | None = None
    completion_timeout_seconds = float(os.getenv("NOVA_SONIC_COMPLETION_TIMEOUT_SECONDS", "20"))
    endpointing_sensitivity = "HIGH" if smoke_mode.startswith("audio_file_en") or smoke_mode.startswith("authorized_generation") or smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation" else "MEDIUM"
    interview_bridge = None
    if smoke_mode == "interview_api_authorized_generation":
        interview_bridge = InterviewBridge(
            InterviewApiClient(settings.api_base_url, settings.internal_api_token),
            turn_save_timeout_seconds=settings.interview_turn_save_timeout_seconds,
            turn_process_timeout_seconds=settings.interview_turn_process_timeout_seconds,
        )

    runtime = NovaSonicRuntime(
        config=NovaSonicRuntimeConfig(
            aws_region=settings.aws_region,
            model_id=settings.nova_sonic_model_id,
            invoke_timeout_seconds=settings.nova_sonic_invoke_timeout_seconds,
            await_output_timeout_seconds=completion_timeout_seconds,
            endpointing_sensitivity=endpointing_sensitivity,
            system_prompt=system_prompt,
            enable_forced_tool_use=smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation",
            forced_tool_result_delay_ms=3000 if smoke_mode == "forced_tool_delayed_result" else 500,
            forced_tool_result_reply_text=(
                MINIMAL_TOOL_REPLY_TEXT
                if smoke_mode == "forced_tool_wire_minimal"
                else APPROVED_REPLY_TEXT
            ),
            interview_timeout_reply_text=settings.interview_timeout_reply_text,
            interview_error_reply_text=settings.interview_error_reply_text,
            interview_unauthorized_reply_text=settings.interview_unauthorized_reply_text,
        ),
        interview_bridge=interview_bridge,
    )
    event_task = asyncio.create_task(_consume_events(runtime))

    runtime_started = False
    text_input_sent = False
    runtime_closed = False
    failed_stage = "none"
    audio_content_started = False
    audio_chunks_sent = 0
    trailing_silence_ms = 0
    silence_frames_during_completion_wait = 0
    silence_task: asyncio.Task[int] | None = None
    silence_stop = asyncio.Event()

    try:
        if smoke_mode in {
            "audio_file_en",
            "audio_file_en_continuous_silence",
            "audio_file_en_shutdown_probe",
            "authorized_generation_wait",
            "authorized_generation_overlap",
            "authorized_generation_prompt_suppression",
            "forced_tool_wire_minimal",
            "forced_tool_authorized_generation",
            "forced_tool_delayed_result",
            "interview_api_authorized_generation",
        }:
            audio_fixture = load_or_generate_pcm(region_name=settings.aws_region, pcm_path=pcm_path)

        await runtime.start(
            VoiceRuntimeContext(
                voice_session_id=voice_session_id,
                record_id=record_id,
                provider="nova_sonic",
            )
        )
        runtime_started = True

        if smoke_mode in {
            "audio_file_en",
            "audio_file_en_continuous_silence",
            "audio_file_en_shutdown_probe",
            "authorized_generation_wait",
            "authorized_generation_overlap",
            "authorized_generation_prompt_suppression",
            "forced_tool_wire_minimal",
            "forced_tool_authorized_generation",
            "forced_tool_delayed_result",
            "interview_api_authorized_generation",
        }:
            await runtime.start_audio_input()
            audio_content_started = True
            audio_chunks_sent += await _stream_pcm_realtime(runtime, audio_fixture)
            silence_chunks = await _stream_trailing_silence(runtime)
            audio_chunks_sent += silence_chunks
            trailing_silence_ms = int(silence_chunks * chunk_duration_seconds(bytes(2048)) * 1000)
            if smoke_mode == "audio_file_en_continuous_silence":
                silence_task = asyncio.create_task(_stream_silence_until_stopped(runtime, silence_stop))
            if smoke_mode.startswith("authorized_generation"):
                transcript_deadline = asyncio.get_running_loop().time() + completion_timeout_seconds
                while asyncio.get_running_loop().time() < transcript_deadline:
                    if runtime.observed_output.user_transcript_received:
                        break
                    await asyncio.sleep(0.1)
                if smoke_mode == "authorized_generation_wait":
                    wait_deadline = asyncio.get_running_loop().time() + completion_timeout_seconds
                    while asyncio.get_running_loop().time() < wait_deadline:
                        observed = runtime.observed_output
                        if observed.unauthorized_completion_count > 0 and observed.completion_status == "output_complete":
                            break
                        await asyncio.sleep(0.1)
                await asyncio.sleep(0.5)
                await runtime.send_reply(
                    AssistantReply(
                        turn_id="approved-turn-1",
                        response_id="approved-response-1",
                        text=APPROVED_REPLY_TEXT,
                        action="approved_reply",
                        question_id=None,
                        state_version=1,
                    )
                )
            if smoke_mode.startswith("forced_tool_"):
                transcript_deadline = asyncio.get_running_loop().time() + completion_timeout_seconds
                while asyncio.get_running_loop().time() < transcript_deadline:
                    observed = runtime.observed_output
                    if observed.tool_result_sent or observed.explicit_stream_error:
                        break
                    await asyncio.sleep(0.1)
        else:
            await runtime.send_reply(
                AssistantReply(
                    turn_id="smoke-turn-1",
                    response_id="smoke-response-1",
                    text=user_text,
                    action="smoke_test",
                    question_id=None,
                    state_version=1,
                )
            )
            text_input_sent = True

        deadline = asyncio.get_running_loop().time() + completion_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            observed = runtime.observed_output
            if smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation":
                if observed.explicit_stream_error:
                    break
            elif observed.completion_end_received or observed.model_stream_error:
                break
            if smoke_mode not in {
                "audio_file_en",
                "audio_file_en_continuous_silence",
                "audio_file_en_shutdown_probe",
                "authorized_generation_wait",
                "authorized_generation_overlap",
                "authorized_generation_prompt_suppression",
                "forced_tool_wire_minimal",
                "forced_tool_authorized_generation",
                "forced_tool_delayed_result",
                "interview_api_authorized_generation",
            } and (
                observed.text_output_received or observed.audio_output_chunks > 0
            ):
                break
            if smoke_mode.startswith("authorized_generation") and (
                observed.approved_output_complete or observed.explicit_stream_error
            ):
                break
            if (smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation") and (
                observed.approved_protocol_complete
                or observed.approved_output_complete
                or observed.explicit_stream_error
            ):
                break
            await asyncio.sleep(0.2)

        observed = runtime.observed_output
        if silence_task is not None:
            silence_stop.set()
            silence_frames_during_completion_wait = await silence_task

        if smoke_mode == "audio_file_en_shutdown_probe" and not observed.completion_end_received:
            await runtime.send_shutdown_probe_events()
            shutdown_deadline = (
                asyncio.get_running_loop().time()
                + float(os.getenv("NOVA_SONIC_SHUTDOWN_COMPLETION_TIMEOUT_SECONDS", "5"))
            )
            while asyncio.get_running_loop().time() < shutdown_deadline:
                observed = runtime.observed_output
                if observed.completion_end_received or observed.model_stream_error:
                    break
                await asyncio.sleep(0.2)

        if (
            smoke_mode in {"audio_file_en", "audio_file_en_continuous_silence", "audio_file_en_shutdown_probe"}
            and not observed.completion_end_received
            and not observed.model_stream_error
            and failed_stage == "none"
        ):
            runtime.mark_completion_wait_timeout()
            if observed.completion_status == "output_complete":
                await runtime.apply_output_complete_grace_period(1.0)
                observed = runtime.observed_output
                failed_stage = observed.failed_stage
            else:
                failed_stage = "completion_timeout"
        elif (
            (smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation")
            and not observed.tool_result_sent
            and not observed.explicit_stream_error
            and failed_stage == "none"
        ):
            failed_stage = "tool_result_wait_timeout"

        if smoke_mode != "audio_file_en_continuous_silence":
            await runtime.end_audio_input()
    except Exception as exc:
        failed_stage = exc.__class__.__name__
    finally:
        if silence_task is not None and not silence_task.done():
            silence_stop.set()
            silence_frames_during_completion_wait = await silence_task
        try:
            await runtime.close()
            runtime_closed = True
        except Exception as exc:
            runtime_closed = False
            if failed_stage == "none":
                failed_stage = f"close:{exc.__class__.__name__}"

    events = await event_task
    runtime_error_events = [event for event in events if isinstance(event, RuntimeError)]
    has_runtime_ready = any(isinstance(event, RuntimeReady) for event in events)
    if runtime_started and not has_runtime_ready and failed_stage == "none":
        failed_stage = "runtime_ready_missing"
    if runtime_error_events and failed_stage == "none":
        failed_stage = str(runtime_error_events[-1].detail.get("code") or "runtime_error")

    observed = runtime.observed_output
    if observed.failed_stage != "none" and failed_stage == "none":
        failed_stage = observed.failed_stage

    assistant_audio = _assistant_audio_from_events(events)
    if assistant_audio:
        Path(ASSISTANT_PCM_PATH).write_bytes(assistant_audio)
        save_pcm_as_wav(pcm=assistant_audio, wav_path=ASSISTANT_WAV_PATH)

    print(f"smoke_mode={smoke_mode}")
    print(f"audio_source={(audio_fixture.source if audio_fixture else 'n/a')}")
    print(f"input_pcm_bytes={(len(audio_fixture.pcm) if audio_fixture else 0)}")
    print(f"input_audio_duration_ms={(audio_fixture.duration_ms if audio_fixture else 0)}")
    print(f"audio_chunks_sent={audio_chunks_sent}")
    print(f"trailing_silence_ms={trailing_silence_ms}")
    print(f"silence_continued_during_completion_wait={str(observed.silence_continued_during_completion_wait).lower()}")
    print(f"silence_frames_during_completion_wait={observed.silence_frames_during_completion_wait or silence_frames_during_completion_wait}")
    print(f"last_input_event={observed.last_input_event}")
    print(f"received_event_types={','.join(observed.received_event_types)}")
    print(f"unknown_event_count={observed.unknown_event_count}")
    print(f"unknown_event_keys={','.join(observed.unknown_event_keys)}")
    print(f"explicit_stream_error={str(observed.explicit_stream_error).lower()}")
    print(f"explicit_stream_error_type={observed.explicit_stream_error_type or 'none'}")
    print(f"explicit_stream_error_message={observed.explicit_stream_error_message or 'none'}")
    print(f"user_transcript_received={str(observed.user_transcript_received).lower()}")
    print(f"tool_use_received={str(observed.tool_use_received).lower()}")
    print(f"tool_output_content_end_received={str(observed.tool_output_content_end_received).lower()}")
    print(f"tool_output_stop_reason={observed.tool_output_stop_reason or 'none'}")
    print(f"tool_result_content_start_sent={str(observed.tool_result_content_start_sent).lower()}")
    print(f"tool_result_sent={str(observed.tool_result_sent).lower()}")
    print(f"tool_result_content_end_sent={str(observed.tool_result_content_end_sent).lower()}")
    print(f"tool_result_sent_after_tool_content_end={str(observed.tool_result_sent_after_tool_content_end).lower()}")
    print(f"tool_use_received_at_ms={observed.tool_use_received_at_ms or -1}")
    print(f"tool_content_end_received_at_ms={observed.tool_content_end_received_at_ms or -1}")
    print(f"tool_result_content_start_sent_at_ms={observed.tool_result_content_start_sent_at_ms or -1}")
    print(f"tool_result_sent_at_ms={observed.tool_result_sent_at_ms or -1}")
    print(f"tool_result_content_end_sent_at_ms={observed.tool_result_content_end_sent_at_ms or -1}")
    print(f"turn_saved={str(observed.turn_saved).lower()}")
    print(f"turn_id_present={str(observed.turn_id_present).lower()}")
    print(f"interview_process_called={str(observed.interview_process_called).lower()}")
    print(f"interview_process_completed={str(observed.interview_process_completed).lower()}")
    print(f"reply_text_present={str(observed.reply_text_present).lower()}")
    print(f"last_sent_event={observed.last_sent_event or 'none'}")
    print(f"last_sent_content_name={observed.last_sent_content_name or 'none'}")
    print(f"last_received_event={observed.last_received_event or 'none'}")
    print(f"completion_start_received={str(observed.completion_start_received).lower()}")
    print(f"post_tool_assistant_text_received={str(observed.post_tool_assistant_text_received).lower()}")
    print(f"post_tool_audio_chunks={observed.post_tool_audio_chunks}")
    print(f"assistant_text_output_received={str(observed.assistant_text_output_received).lower()}")
    print(f"assistant_audio_chunks={observed.audio_output_chunks}")
    print(f"assistant_final_text_received={str(observed.assistant_final_text_received).lower()}")
    print(f"completion_end_received={str(observed.completion_end_received).lower()}")
    print(f"completion_stop_reason={observed.completion_stop_reason or 'none'}")
    print(f"completion_wait_timeout={str(observed.completion_wait_timeout).lower()}")
    print(f"audio_content_end_sent_at_ms={observed.audio_content_end_sent_at_ms or -1}")
    print(f"prompt_end_sent_at_ms={observed.prompt_end_sent_at_ms or -1}")
    print(f"session_end_sent_at_ms={observed.session_end_sent_at_ms or -1}")
    print(f"completion_end_received_at_ms={observed.completion_end_received_at_ms or -1}")
    print(f"completion_end_after_session_end={str(observed.completion_end_after_session_end).lower()}")
    print(f"model_stream_error={str(observed.model_stream_error).lower()}")
    print(f"runtime_closed={str(runtime_closed).lower()}")
    print(f"approved_output_complete={str(observed.approved_output_complete).lower()}")
    print(f"failed_stage={failed_stage}")
    print(f"spontaneous_completion_started={str(observed.spontaneous_completion_started).lower()}")
    print(f"unauthorized_completion_count={observed.unauthorized_completion_count}")
    print(f"unauthorized_audio_chunks={observed.unauthorized_audio_chunks}")
    print(f"approved_reply_sent={str(observed.approved_reply_sent).lower()}")
    print(f"approved_completion_started={str(observed.approved_completion_started).lower()}")
    print(f"approved_completion_id={observed.approved_completion_id or 'none'}")
    print(f"approved_audio_chunks={observed.audio_output_chunks}")
    print(f"approved_final_text_received={str(observed.assistant_final_text_received).lower()}")
    print(f"approved_output_complete={str(observed.approved_output_complete).lower()}")
    print(f"approved_protocol_complete={str(observed.approved_protocol_complete).lower()}")
    print(f"planned_reply_length={observed.planned_reply_length}")
    print(f"spoken_transcript_length={observed.spoken_transcript_length}")
    print(f"spoken_matches_exactly={str(observed.spoken_matches_exactly).lower()}")
    print(f"spoken_contains_planned_reply={str(observed.spoken_contains_planned_reply).lower()}")
    print(f"tool_use_received={str(observed.tool_use_received).lower()}")
    print(f"tool_use_completion_matches={str(observed.tool_use_completion_matches).lower()}")
    print(f"tool_result_sent={str(observed.tool_result_sent).lower()}")
    print(f"tool_result_delay_ms={observed.tool_result_delay_ms}")
    print(f"pre_tool_assistant_text_count={observed.pre_tool_assistant_text_count}")
    print(f"pre_tool_audio_chunks={observed.pre_tool_audio_chunks}")
    print(f"post_tool_assistant_text_received={str(observed.post_tool_assistant_text_received).lower()}")
    print(f"post_tool_audio_chunks={observed.post_tool_audio_chunks}")

    if smoke_mode.startswith("authorized_generation"):
        if runtime_started and runtime_closed and failed_stage == "none":
            if (
                observed.user_transcript_received
                and observed.approved_reply_sent
                and observed.approved_completion_started
                and observed.approved_output_complete
                and not observed.explicit_stream_error
            ):
                return 0
    if smoke_mode.startswith("forced_tool_") or smoke_mode == "interview_api_authorized_generation":
        if runtime_started and runtime_closed and failed_stage == "none":
            if (
                observed.user_transcript_received
                and observed.tool_use_received
                and observed.tool_output_content_end_received
                and observed.tool_result_sent
                and observed.tool_result_sent_after_tool_content_end
                and (observed.post_tool_assistant_text_received or observed.post_tool_audio_chunks > 0)
                and observed.approved_protocol_complete
                and not observed.explicit_stream_error
            ):
                return 0
    if smoke_mode in {"audio_file_en", "audio_file_en_continuous_silence", "audio_file_en_shutdown_probe"}:
        if runtime_started and runtime_closed and failed_stage == "none":
            if observed.user_transcript_received and observed.completion_start_received:
                if observed.assistant_text_output_received or observed.audio_output_chunks > 0:
                    if observed.completion_end_received:
                        return 0
                    if observed.completion_protocol_degraded:
                        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
