"""
Role:
    Nova 2 Sonicの非同期tool calling経路を実ストリームで検証するprobe。

Summary:
    audio inputを開いたままtoolUse待機中のcross-modal textと、元のtoolUseIdへの
    toolResult返却を順番に実行し、出力イベントと音声生成をJSON Linesで記録する。

Relations:
    Uses nova_sonic.sdk_client, protocol payload/event helpers. Independent from NovaSonicRuntime.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.runtimes.nova_sonic.protocol.events import (
    AudioOutputEvent,
    CompletionEndEvent,
    CompletionStartEvent,
    ContentEndEvent,
    ContentStartEvent,
    ErrorEvent,
    TextOutputEvent,
    ToolUseEvent,
    decode_output_bytes,
)
from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import (
    build_audio_end_sequence,
    build_audio_input_event,
    build_audio_start_sequence,
    build_prompt_end_event,
    build_runtime_start_sequence,
    build_session_end_event,
    build_tool_result_sequence,
    build_user_text_sequence,
)
from ai_interviewer_voice.runtimes.nova_sonic.sdk_client import (
    create_bedrock_runtime_client,
    open_bidirectional_stream,
    send_payload,
)

from smoke_nova_sonic_helpers import chunk_duration_seconds, iter_pcm_chunks, load_or_generate_pcm


TOOL_NAME = "process_interview_turn"
PREFACE_TEXT = "確認します。"
FOLLOWUP_TEXT = "回答内容を判断できませんでした。もう一度教えてください。"
PREFACE_QUIET_GUARD_SECONDS = 0.5
SYSTEM_PROMPT = """
You are a voice assistant testing asynchronous tool calling.
Call process_interview_turn only when the user explicitly asks you to process an interview answer.
Keep that tool call pending until its toolResult arrives.
While the tool is pending, if you receive interactive USER text, speak that text immediately
without starting another tool call and without resolving the pending tool call. A short status
message such as "確認します。" is never a request to call a tool.
After the toolResult arrives, speak only the reply_text value from the result.
Do not add, omit, or rephrase words.
""".strip()


def _log(event: str, **detail: object) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "monotonic_timestamp_ms": round(monotonic() * 1000),
                **detail,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


@dataclass
class ProbeState:
    preface_mode: str = "cross_modal"
    tool_completion_id: str | None = None
    tool_use_id: str | None = None
    preface_content_name: str | None = None
    tool_result_content_name: str | None = None
    completion_ids: list[str] = field(default_factory=list)
    preface_audio: bytearray = field(default_factory=bytearray)
    followup_audio: bytearray = field(default_factory=bytearray)
    preface_audio_started: bool = False
    preface_audio_ended: bool = False
    local_preface_completed: bool = False
    tool_result_dispatching: bool = False
    tool_result_sent: bool = False
    followup_audio_started: bool = False
    followup_audio_ended: bool = False
    stream_error: str | None = None
    active_output_content_id: str | None = None
    active_audio_phase: str | None = None
    preface_text_outputs: list[str] = field(default_factory=list)
    followup_text_outputs: list[str] = field(default_factory=list)
    preface_guard_task: asyncio.Task[None] | None = None
    evaluation_ready: asyncio.Event = field(default_factory=asyncio.Event)
    preface_output_complete: asyncio.Event = field(default_factory=asyncio.Event)
    probe_complete: asyncio.Event = field(default_factory=asyncio.Event)
    completion_end_received: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def succeeded(self) -> bool:
        preface_succeeded = (
            self.local_preface_completed
            if self.preface_mode == "local"
            else PREFACE_TEXT in "".join(self.preface_text_outputs)
            and bool(self.preface_audio)
        )
        return bool(
            self.tool_use_id
            and preface_succeeded
            and self.tool_result_sent
            and self.followup_audio
            and self.followup_audio_ended
            and self.stream_error is None
        )


async def _send_sequence(stream: object, sequence: list[tuple[str, dict]]) -> None:
    for stage, payload in sequence:
        await send_payload(stream, payload)
        _log("input_event_sent", stage=stage)


async def _run_evaluation(state: ProbeState, delay_seconds: float) -> None:
    _log("evaluation_started", delay_seconds=delay_seconds)
    await asyncio.sleep(delay_seconds)
    state.evaluation_ready.set()
    _log("evaluation_completed")


async def _mark_preface_output_complete_after_guard(
    state: ProbeState,
    *,
    content_id: str | None,
) -> None:
    try:
        await asyncio.sleep(PREFACE_QUIET_GUARD_SECONDS)
    except asyncio.CancelledError:
        _log("preface_quiet_guard_cancelled", content_id=content_id)
        raise
    state.preface_audio_ended = True
    state.preface_output_complete.set()
    _log(
        "preface_output_complete",
        content_id=content_id,
        quiet_guard_ms=round(PREFACE_QUIET_GUARD_SECONDS * 1000),
        bytes=len(state.preface_audio),
    )


async def _play_local_preface(
    state: ProbeState,
    *,
    pcm: bytes,
    sample_rate_hz: int,
) -> None:
    duration_ms = round(len(pcm) / (sample_rate_hz * 2) * 1000)
    _log(
        "local_preface_playback_started",
        text=PREFACE_TEXT,
        bytes=len(pcm),
        sample_rate_hz=sample_rate_hz,
        audio_duration_ms=duration_ms,
    )
    await asyncio.sleep(duration_ms / 1000)
    state.local_preface_completed = True
    state.preface_output_complete.set()
    _log("local_preface_playback_ended", audio_duration_ms=duration_ms)


async def _send_tool_result_when_ready(
    *,
    stream: object,
    prompt_name: str,
    state: ProbeState,
) -> None:
    await state.evaluation_ready.wait()
    await state.preface_output_complete.wait()
    if state.tool_use_id is None or state.tool_result_sent:
        return
    state.tool_result_content_name = f"tool-result-{uuid4()}"
    followup_text = os.getenv("NOVA_ASYNC_TOOL_FOLLOWUP_TEXT", FOLLOWUP_TEXT)
    _log(
        "tool_result_send_started",
        tool_use_id=state.tool_use_id,
        content_name=state.tool_result_content_name,
        tool_completion_id=state.tool_completion_id,
    )
    state.tool_result_dispatching = True
    try:
        await _send_sequence(
            stream,
            build_tool_result_sequence(
                prompt_name=prompt_name,
                content_name=state.tool_result_content_name,
                tool_use_id=state.tool_use_id,
                result={"reply_text": followup_text},
            ),
        )
    finally:
        state.tool_result_dispatching = False
    state.tool_result_sent = True
    _log(
        "tool_result_send_completed",
        tool_use_id=state.tool_use_id,
        content_name=state.tool_result_content_name,
    )


async def _handle_protocol_event(
    *,
    event: object,
    stream: object,
    prompt_name: str,
    state: ProbeState,
    evaluation_delay_seconds: float,
) -> None:
    common = {
        "completion_id": getattr(event, "completion_id", None),
        "content_id": getattr(event, "content_id", None),
    }
    if isinstance(event, CompletionStartEvent):
        if event.completion_id is not None:
            state.completion_ids.append(event.completion_id)
        _log("completion_start_received", **common)
        return
    if isinstance(event, ContentStartEvent):
        state.active_output_content_id = event.content_id
        if event.modality == "AUDIO":
            state.active_audio_phase = (
                "tool_result"
                if state.tool_result_dispatching or state.tool_result_sent
                else "preface"
            )
            if state.active_audio_phase == "preface" and state.preface_guard_task is not None:
                state.preface_guard_task.cancel()
                state.preface_guard_task = None
        _log(
            "content_start_received",
            **common,
            role=event.role,
            modality=event.modality,
            generation_stage=event.generation_stage,
        )
        return
    if isinstance(event, TextOutputEvent):
        if state.tool_result_sent:
            state.followup_text_outputs.append(event.text)
        else:
            state.preface_text_outputs.append(event.text)
        _log(
            "text_output_received",
            **common,
            generation_stage=event.generation_stage,
            text=event.text,
        )
        return
    if isinstance(event, ToolUseEvent):
        _log(
            "tool_use_received",
            **common,
            tool_name=event.tool_name,
            tool_use_id=event.tool_use_id,
        )
        if state.tool_use_id is not None:
            return
        if not event.tool_use_id:
            state.stream_error = "tool_use_id_missing"
            state.probe_complete.set()
            return
        state.tool_completion_id = event.completion_id
        state.tool_use_id = event.tool_use_id
        asyncio.create_task(_run_evaluation(state, evaluation_delay_seconds))
        if state.preface_mode == "cross_modal":
            state.preface_content_name = f"preface-{uuid4()}"
            _log(
                "preface_text_send_started",
                content_name=state.preface_content_name,
                tool_use_id=state.tool_use_id,
                tool_completion_id=state.tool_completion_id,
            )
            await _send_sequence(
                stream,
                build_user_text_sequence(
                    prompt_name=prompt_name,
                    content_name=state.preface_content_name,
                    text=PREFACE_TEXT,
                ),
            )
            _log("preface_text_send_completed", content_name=state.preface_content_name)
        else:
            local_pcm_path = Path(os.environ["NOVA_ASYNC_TOOL_LOCAL_PREFACE_PCM_PATH"])
            local_sample_rate_hz = int(
                os.getenv("NOVA_ASYNC_TOOL_LOCAL_PREFACE_SAMPLE_RATE_HZ", "24000")
            )
            asyncio.create_task(
                _play_local_preface(
                    state,
                    pcm=local_pcm_path.read_bytes(),
                    sample_rate_hz=local_sample_rate_hz,
                )
            )
        asyncio.create_task(
            _send_tool_result_when_ready(
                stream=stream,
                prompt_name=prompt_name,
                state=state,
            )
        )
        return
    if isinstance(event, AudioOutputEvent):
        if not event.audio_bytes:
            return
        if state.active_audio_phase == "preface":
            if not state.preface_audio_started:
                state.preface_audio_started = True
                _log("assistant_audio_first_chunk", phase="preface", bytes=len(event.audio_bytes), **common)
            state.preface_audio.extend(event.audio_bytes)
        elif state.active_audio_phase == "tool_result":
            if not state.followup_audio_started:
                state.followup_audio_started = True
                _log("assistant_audio_first_chunk", phase="tool_result", bytes=len(event.audio_bytes), **common)
            state.followup_audio.extend(event.audio_bytes)
        return
    if isinstance(event, ContentEndEvent):
        _log(
            "content_end_received",
            **common,
            role=event.role,
            modality=event.modality,
            stop_reason=event.stop_reason,
            generation_stage=event.generation_stage,
        )
        if event.content_id == state.active_output_content_id:
            state.active_output_content_id = None
        if event.modality == "AUDIO" and state.active_audio_phase == "preface":
            _log("assistant_speech_ended", phase="preface", bytes=len(state.preface_audio), **common)
            state.active_audio_phase = None
            if state.preface_guard_task is not None:
                state.preface_guard_task.cancel()
            state.preface_guard_task = asyncio.create_task(
                _mark_preface_output_complete_after_guard(
                    state,
                    content_id=event.content_id,
                )
            )
        elif event.modality == "AUDIO" and state.active_audio_phase == "tool_result":
            state.followup_audio_ended = True
            state.active_audio_phase = None
            state.probe_complete.set()
            _log("assistant_speech_ended", phase="tool_result", bytes=len(state.followup_audio), **common)
        return
    if isinstance(event, CompletionEndEvent):
        state.completion_end_received.set()
        _log("completion_end_received", stop_reason=event.stop_reason, **common)
        return
    if isinstance(event, ErrorEvent):
        state.stream_error = f"{event.code}:{event.message}"
        state.probe_complete.set()
        _log("stream_error", code=event.code, message=event.message, **common)


async def _receive_output(
    *,
    stream: object,
    prompt_name: str,
    state: ProbeState,
    evaluation_delay_seconds: float,
) -> None:
    try:
        await stream.await_output()
        output_stream = stream.output_stream
        if output_stream is None:
            raise RuntimeError("output_stream_missing")
        async for raw_event in output_stream:
            value = getattr(raw_event, "value", None)
            payload = getattr(value, "bytes_", None)
            if isinstance(payload, bytes):
                event = decode_output_bytes(payload, active_content_id=state.active_output_content_id)
                await _handle_protocol_event(
                    event=event,
                    stream=stream,
                    prompt_name=prompt_name,
                    state=state,
                    evaluation_delay_seconds=evaluation_delay_seconds,
                )
                continue
            state.stream_error = type(raw_event).__name__
            state.probe_complete.set()
            _log("stream_error", exception_type=type(raw_event).__name__)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.stream_error = f"{type(exc).__name__}:{exc}"
        state.probe_complete.set()
        _log("stream_error", exception_type=type(exc).__name__, message=str(exc))


async def _send_audio_until_complete(
    *,
    stream: object,
    prompt_name: str,
    audio_content_name: str,
    pcm: bytes,
    state: ProbeState,
) -> None:
    for chunk in iter_pcm_chunks(pcm):
        await send_payload(
            stream,
            build_audio_input_event(
                prompt_name=prompt_name,
                content_name=audio_content_name,
                pcm=chunk,
            ),
        )
        await asyncio.sleep(chunk_duration_seconds(chunk))
    silence = bytes(1024)
    while not state.probe_complete.is_set():
        await send_payload(
            stream,
            build_audio_input_event(
                prompt_name=prompt_name,
                content_name=audio_content_name,
                pcm=silence,
            ),
        )
        await asyncio.sleep(0.032)


async def run_probe() -> bool:
    if os.getenv("RUN_NOVA_ASYNC_TOOL_PROBE") != "1":
        print("Set RUN_NOVA_ASYNC_TOOL_PROBE=1 to run the real Nova stream probe.")
        return True

    prompt_name = f"async-tool-probe-{uuid4()}"
    system_content_name = f"system-{uuid4()}"
    audio_content_name = f"audio-{uuid4()}"
    evaluation_delay_seconds = float(os.getenv("NOVA_ASYNC_TOOL_EVALUATION_DELAY_SECONDS", "1.5"))
    timeout_seconds = float(os.getenv("NOVA_ASYNC_TOOL_PROBE_TIMEOUT_SECONDS", "30"))
    fixture = load_or_generate_pcm(
        region_name=settings.aws_region,
        pcm_path=os.getenv("NOVA_ASYNC_TOOL_PCM_PATH"),
    )
    preface_mode = os.getenv("NOVA_ASYNC_TOOL_PREFACE_MODE", "cross_modal")
    if preface_mode not in {"cross_modal", "local"}:
        raise ValueError("NOVA_ASYNC_TOOL_PREFACE_MODE must be cross_modal or local")
    if preface_mode == "local" and not os.getenv("NOVA_ASYNC_TOOL_LOCAL_PREFACE_PCM_PATH"):
        raise ValueError("NOVA_ASYNC_TOOL_LOCAL_PREFACE_PCM_PATH is required in local mode")
    state = ProbeState(preface_mode=preface_mode)
    stream = None
    receiver_task: asyncio.Task[None] | None = None
    audio_task: asyncio.Task[None] | None = None
    try:
        client = create_bedrock_runtime_client(settings.aws_region)
        stream = await open_bidirectional_stream(
            client,
            model_id=settings.nova_sonic_model_id,
            timeout_seconds=10,
        )
        start_sequence = build_runtime_start_sequence(
            prompt_name=prompt_name,
            system_content_name=system_content_name,
            system_prompt=SYSTEM_PROMPT,
            endpointing_sensitivity="HIGH",
            voice_id="matthew",
            forced_tool_name=TOOL_NAME,
        )
        tool_choice = os.getenv("NOVA_ASYNC_TOOL_CHOICE", "auto")
        if tool_choice == "auto":
            tool_configuration = start_sequence[1][1]["event"]["promptStart"][
                "toolConfiguration"
            ]
            tool_configuration["toolChoice"] = {"auto": {}}
            tool_configuration["tools"][0]["toolSpec"]["description"] = (
                "Process a spoken interview answer only when the user explicitly asks "
                "to process that answer. Do not call this tool for status messages."
            )
        elif tool_choice != "forced":
            raise ValueError("NOVA_ASYNC_TOOL_CHOICE must be auto or forced")
        _log("tool_choice_configured", tool_choice=tool_choice)
        await _send_sequence(
            stream,
            start_sequence,
        )
        await _send_sequence(
            stream,
            build_audio_start_sequence(
                prompt_name=prompt_name,
                content_name=audio_content_name,
            ),
        )
        _log(
            "audio_input_opened",
            content_name=audio_content_name,
            source=fixture.source,
            duration_ms=fixture.duration_ms,
        )
        receiver_task = asyncio.create_task(
            _receive_output(
                stream=stream,
                prompt_name=prompt_name,
                state=state,
                evaluation_delay_seconds=evaluation_delay_seconds,
            )
        )
        audio_task = asyncio.create_task(
            _send_audio_until_complete(
                stream=stream,
                prompt_name=prompt_name,
                audio_content_name=audio_content_name,
                pcm=fixture.pcm,
                state=state,
            )
        )
        await asyncio.wait_for(state.probe_complete.wait(), timeout=timeout_seconds)
    except TimeoutError:
        state.stream_error = "probe_timeout"
        _log("probe_timeout", timeout_seconds=timeout_seconds)
    finally:
        state.probe_complete.set()
        if audio_task is not None:
            audio_task.cancel()
            try:
                await audio_task
            except asyncio.CancelledError:
                pass
        if state.preface_guard_task is not None and not state.preface_guard_task.done():
            state.preface_guard_task.cancel()
            with suppress(asyncio.CancelledError):
                await state.preface_guard_task
        if stream is not None:
            async def send_shutdown_events() -> None:
                await _send_sequence(
                    stream,
                    build_audio_end_sequence(
                        prompt_name=prompt_name,
                        content_name=audio_content_name,
                    ),
                )
                await send_payload(stream, build_prompt_end_event(prompt_name))
                _log("input_event_sent", stage="prompt_end_sent")
                await send_payload(stream, build_session_end_event())
                _log("input_event_sent", stage="session_end_sent")

            try:
                await asyncio.wait_for(send_shutdown_events(), timeout=2)
                await asyncio.wait_for(state.completion_end_received.wait(), timeout=5)
            except TimeoutError:
                _log("completion_end_wait_timeout")
            except Exception as exc:
                _log(
                    "shutdown_send_failed",
                    exception_type=type(exc).__name__,
                    message=str(exc),
                )
            if receiver_task is not None and not receiver_task.done():
                receiver_task.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver_task
            with suppress(Exception):
                await asyncio.wait_for(stream.input_stream.close(), timeout=2)
            if stream.output_stream is not None:
                with suppress(Exception):
                    await asyncio.wait_for(stream.output_stream.close(), timeout=2)
            with suppress(Exception):
                await asyncio.wait_for(stream.close(), timeout=2)

    output_dir = Path(os.getenv("NOVA_ASYNC_TOOL_OUTPUT_DIR", "/tmp"))
    if state.preface_audio:
        (output_dir / "nova-async-tool-preface.pcm").write_bytes(state.preface_audio)
    if state.followup_audio:
        (output_dir / "nova-async-tool-followup.pcm").write_bytes(state.followup_audio)
    _log(
        "probe_result",
        succeeded=state.succeeded,
        preface_mode=state.preface_mode,
        tool_completion_id=state.tool_completion_id,
        tool_use_id=state.tool_use_id,
        preface_content_name=state.preface_content_name,
        tool_result_content_name=state.tool_result_content_name,
        completion_ids=state.completion_ids,
        preface_audio_bytes=len(state.preface_audio),
        preface_text_outputs=state.preface_text_outputs,
        followup_audio_bytes=len(state.followup_audio),
        followup_text_outputs=state.followup_text_outputs,
        completion_end_received=state.completion_end_received.is_set(),
        stream_error=state.stream_error,
    )
    return state.succeeded


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(run_probe()) else 1)
