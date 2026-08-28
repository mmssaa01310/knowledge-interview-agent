from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.polly_synthesizer import (
    PollySynthesisError,
)
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import (
    LISTEN_ACK_TEXT,
    AssistantResponseState,
    TranscribePollyRuntime,
)
from ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream import (
    TranscribeResult,
)
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantBackchannel,
    AssistantInterrupted,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    InputStateChanged,
    RuntimeError,
    UserTranscriptFinal,
)
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


def _pcm(sample: int, samples: int = 320) -> bytes:
    return b"".join(sample.to_bytes(2, "little", signed=True) for _ in range(samples))


def _frame(sample: int) -> AudioFrame:
    return AudioFrame(
        pcm=_pcm(sample),
        sample_rate_hz=16000,
        channels=1,
    )


class FakeTranscribe:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.on_result = None
        self.on_fatal_error = None
        self.closed = False

    async def start(self, *, on_result, on_reconnecting, on_fatal_error) -> None:
        self.on_result = on_result
        self.on_fatal_error = on_fatal_error

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def close(self) -> None:
        self.closed = True

    async def result(
        self,
        text: str,
        *,
        stable_text: str = "",
        is_partial: bool = True,
        result_id: str = "result-1",
    ) -> None:
        assert self.on_result is not None
        await self.on_result(
            TranscribeResult(
                text=text,
                stable_text=stable_text,
                is_partial=is_partial,
                result_id=result_id,
            )
        )


class FakePolly:
    def __init__(self) -> None:
        self.cache = {
            LISTEN_ACK_TEXT: bytes(640),
            "回答を確認しています。": bytes(640),
            "確認に少し時間がかかっています。": bytes(640),
        }
        self.calls: list[str] = []
        self.release: asyncio.Event | None = None

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.release is not None:
            await self.release.wait()
        return bytes(640)

    async def get_cached(self, text: str) -> bytes | None:
        return self.cache.get(text)

    async def warm(self, texts: tuple[str, ...]) -> None:
        return None


class FailingPolly(FakePolly):
    async def synthesize(self, text: str) -> bytes:
        raise PollySynthesisError("polly unavailable")


class EmptyPolly(FakePolly):
    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return b""


class WarmFailingPolly(FakePolly):
    async def warm(self, texts: tuple[str, ...]) -> None:
        raise RuntimeError("warm failed")


class OrderedPolly(FakePolly):
    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if text.startswith("最初"):
            await asyncio.sleep(0.02)
            return (100).to_bytes(2, "little", signed=True) * 320
        return (200).to_bytes(2, "little", signed=True) * 320


class LongPolly(FakePolly):
    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return bytes(32000)


class StreamingPolly(FakePolly):
    def __init__(self) -> None:
        super().__init__()
        self.release_following = asyncio.Event()
        self.following_cancelled = False

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if len(self.calls) == 1:
            return (100).to_bytes(2, "little", signed=True) * 320
        try:
            await self.release_following.wait()
        except asyncio.CancelledError:
            self.following_cancelled = True
            raise
        return (200).to_bytes(2, "little", signed=True) * 320


class FakeBridge:
    def __init__(self) -> None:
        self.process_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self.assistant_events: list[dict] = []
        self.intent_calls: list[dict] = []
        self.state_version = 1

    async def load_voice_session(self, voice_session_id: str):
        return SimpleNamespace(
            current_question_id="q-1",
            state_version=self.state_version,
            interview_status="active",
        )

    async def process_turn(self, **kwargs) -> InterviewBridgeResult:
        self.process_calls.append(kwargs)
        return InterviewBridgeResult(
            turn_id="turn-1",
            response_id="response-1",
            reply_text="ありがとうございます。次の質問です。",
            action="NEXT_QUESTION",
            question_id="q-2",
            state_version=2,
            interview_status="active",
        )

    async def classify_turn_intent(self, **kwargs):
        self.intent_calls.append(kwargs)
        return SimpleNamespace(turn_type="ANSWER")

    async def create_assistant_event(self, **kwargs) -> None:
        self.assistant_events.append(kwargs)

    async def cancel_turn(self, **kwargs) -> None:
        self.cancel_calls.append(kwargs)
        self.state_version += 1


class BlockingBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.process_started = asyncio.Event()
        self.release_process = asyncio.Event()

    async def process_turn(self, **kwargs) -> InterviewBridgeResult:
        self.process_calls.append(kwargs)
        self.process_started.set()
        await self.release_process.wait()
        return await super().process_turn(**kwargs)


class ControlBridge(FakeBridge):
    async def process_turn(self, **kwargs) -> InterviewBridgeResult:
        result = await super().process_turn(**kwargs)
        return InterviewBridgeResult(
            turn_id=result.turn_id,
            response_id=result.response_id,
            reply_text=result.reply_text,
            action=result.action,
            question_id=result.question_id,
            state_version=result.state_version,
            interview_status=result.interview_status,
            retrieval_policy=result.retrieval_policy,
            retrieval_executed=result.retrieval_executed,
            turn_type="CONTROL",
        )


def _config() -> TranscribePollyRuntimeConfig:
    return TranscribePollyRuntimeConfig(
        listen_ack_silence_ms=30,
        normal_endpoint_ms=80,
        hard_endpoint_ms=120,
        final_result_wait_ms=20,
        final_result_settle_ms=0,
        listen_ack_min_speech_ms=40,
        listen_ack_min_stable_chars=5,
        backchannel_cooldown_ms=0,
        backchannel_enabled=True,
        processing_ack_delay_ms=1000,
        long_processing_notice_ms=2000,
    )


async def _collect_until(runtime: TranscribePollyRuntime, predicate, timeout=1.0):
    collected = []

    async def collect():
        async for event in runtime.events():
            collected.append(event)
            if predicate(event):
                return

    await asyncio.wait_for(collect(), timeout=timeout)
    return collected


@pytest.mark.anyio
async def test_runtime_batches_five_twenty_ms_frames_for_transcribe() -> None:
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )

    for _ in range(5):
        await runtime.push_audio(_frame(0))

    assert len(transcribe.audio) == 1
    assert len(transcribe.audio[0]) == 3200
    await runtime.close()


@pytest.mark.anyio
async def test_five_hundred_ms_pause_ack_does_not_finalize_turn() -> None:
    transcribe = FakeTranscribe()
    bridge = FakeBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.push_audio(_frame(1200))
    await runtime.push_audio(_frame(1200))
    await transcribe.result("設備が停止して", stable_text="設備が停止して")
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.09)

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert any(isinstance(event, AssistantBackchannel) for event in events)
    assert not any(isinstance(event, UserTranscriptFinal) for event in events)
    assert bridge.process_calls == []

    await runtime.push_audio(_frame(1200))
    assert bridge.process_calls == []
    await runtime.close()


@pytest.mark.anyio
async def test_backchannels_are_disabled_by_default() -> None:
    polly = FakePolly()
    runtime = TranscribePollyRuntime(
        config=TranscribePollyRuntimeConfig(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    runtime._voiced_duration_ms = 1000
    await runtime._maybe_play_listen_ack("設備を担当しています")

    assert polly.calls == []
    assert not any(
        isinstance(event, AssistantBackchannel)
        for event in runtime._events._queue
    )
    await runtime.close()


@pytest.mark.anyio
async def test_initial_reply_preload_is_reused_by_formal_audio() -> None:
    polly = FakePolly()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )

    await runtime.prepare_initial_reply("これからインタビューを開始します。最初の質問です。")
    audio = [
        chunk
        async for chunk in runtime._synthesize_chunks(
            "これからインタビューを開始します。最初の質問です。",
            generation=0,
        )
    ]

    assert len(audio) == 2
    assert polly.calls == ["これからインタビューを開始します。", "最初の質問です。"]
    await runtime.close()


@pytest.mark.anyio
async def test_final_transcript_is_the_only_text_sent_to_interview_bridge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    transcribe = FakeTranscribe()
    bridge = FakeBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.push_audio(_frame(1200))
    await transcribe.result("途中です", stable_text="途中です", is_partial=True)
    await asyncio.sleep(0)
    assert bridge.process_calls == []

    await transcribe.result("最終回答です", is_partial=False)
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.11)

    assert len(bridge.process_calls) == 1
    assert bridge.process_calls[0]["transcript"] == "最終回答です"
    assert bridge.process_calls[0]["turn_type"] == "ANSWER"
    assert bridge.process_calls[0]["answer_to_question_id"] == "q-1"
    assert bridge.intent_calls == []
    assert "voice_turn_api_completed" in caplog.text
    assert "voice_turn_polly_started" in caplog.text
    assert "voice_turn_polly_first_chunk_ready" in caplog.text
    assert "voice_turn_pipeline_latency" in caplog.text
    await runtime.close()


@pytest.mark.anyio
async def test_control_transcript_is_classified_by_api_before_commit() -> None:
    transcribe = FakeTranscribe()
    bridge = ControlBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.push_audio(_frame(1200))
    await transcribe.result("会話を終了してください", is_partial=False)
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.11)

    assert bridge.process_calls[0]["turn_type"] == "ANSWER"
    assert bridge.process_calls[0]["answer_to_question_id"] == "q-1"
    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    final = next(event for event in events if isinstance(event, UserTranscriptFinal))
    assert final.turn_type == "CONTROL"
    assert final.question_id is None
    await runtime.close()


@pytest.mark.anyio
async def test_new_user_generation_discards_delayed_polly_audio() -> None:
    transcribe = FakeTranscribe()
    polly = FakePolly()
    polly.release = asyncio.Event()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    reply_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id="turn-1",
                response_id="response-old",
                text="古い回答です。",
                action="NEXT_QUESTION",
                question_id="q-2",
                state_version=2,
            )
        )
    )
    for _ in range(20):
        if polly.calls:
            break
        await asyncio.sleep(0)
    assert polly.calls
    await runtime.push_audio(_frame(1200))
    polly.release.set()
    await reply_task

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert any(isinstance(event, AssistantTranscriptFinal) for event in events)
    assert not any(
        isinstance(event, AssistantAudioChunk)
        and event.response_id == "response-old"
        for event in events
    )
    await runtime.close()


@pytest.mark.anyio
async def test_formal_reply_waits_for_previous_browser_playback_to_drain() -> None:
    polly = FakePolly()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )

    await runtime.send_reply(
        SimpleNamespace(
            turn_id="turn-first",
            response_id="response-first",
            text="最初の回答です。",
            action="NEXT_QUESTION",
            question_id="q-2",
            state_version=2,
        )
    )
    second_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id="turn-second",
                response_id="response-second",
                text="次の質問です。",
                action="NEXT_QUESTION",
                question_id="q-3",
                state_version=3,
            )
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert second_task.done() is False
    assert polly.calls == ["最初の回答です。"]

    await runtime.notify_assistant_playback_drained(
        response_id="response-first",
        generation=runtime._generation,
    )
    await second_task
    assert polly.calls == ["最初の回答です。", "次の質問です。"]
    await runtime.close()


@pytest.mark.anyio
async def test_barge_in_emits_interrupted_after_120ms_voice() -> None:
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    runtime._assistant_speaking = True
    for _ in range(6):
        await runtime.push_audio(_frame(1200))

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert any(isinstance(event, AssistantInterrupted) for event in events)
    await runtime.close()


@pytest.mark.anyio
async def test_parallel_polly_generation_is_emitted_in_text_order() -> None:
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=OrderedPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.send_reply(
        SimpleNamespace(
            turn_id="turn-1",
            response_id="response-ordered",
            text="最初の文章です。二番目の文章です。",
            action="NEXT_QUESTION",
            question_id="q-2",
            state_version=2,
        )
    )

    audio = []
    while not runtime._events.empty():
        event = runtime._events.get_nowait()
        if isinstance(event, AssistantAudioChunk):
            audio.append(event)
    assert len(audio) == 2
    assert int.from_bytes(audio[0].pcm[:2], "little", signed=True) == 100
    assert int.from_bytes(audio[1].pcm[:2], "little", signed=True) == 200
    await runtime.close()


@pytest.mark.anyio
async def test_polly_failure_keeps_formal_text_and_emits_nonfatal_error() -> None:
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=FailingPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.send_reply(
        SimpleNamespace(
            turn_id="turn-1",
            response_id="response-text-only",
            text="テキストでは回答を継続します。",
            action="NEXT_QUESTION",
            question_id="q-2",
            state_version=2,
        )
    )

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert any(
        isinstance(event, AssistantTranscriptFinal)
        and event.response_id == "response-text-only"
        for event in events
    )
    assert any(
        isinstance(event, RuntimeError)
        and event.message == "polly_synthesis_failed"
        and event.fatal is False
        for event in events
    )
    assert any(
        isinstance(event, InputStateChanged)
        and event.input_state == "ANSWER_LISTENING"
        for event in events
    )
    assert not any(
        isinstance(event, AssistantAudioChunk)
        and event.response_id == "response-text-only"
        for event in events
    )
    await runtime.close()


@pytest.mark.anyio
async def test_empty_polly_output_reopens_input_after_formal_text() -> None:
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=EmptyPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )

    await runtime.send_reply(
        SimpleNamespace(
            turn_id="turn-1",
            response_id="response-empty-audio",
            text="音声が空でもテキスト回答は表示されます。",
            action="NEXT_QUESTION",
            question_id="q-2",
            state_version=2,
        )
    )

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert any(
        isinstance(event, AssistantTranscriptFinal)
        and event.response_id == "response-empty-audio"
        for event in events
    )
    assert any(
        isinstance(event, AssistantSpeechEnded)
        and event.response_id == "response-empty-audio"
        and event.audio_duration_ms == 0
        for event in events
    )
    assert any(
        isinstance(event, InputStateChanged)
        and event.input_state == "ANSWER_LISTENING"
        for event in events
    )
    assert not any(
        isinstance(event, AssistantAudioChunk)
        and event.response_id == "response-empty-audio"
        for event in events
    )
    await runtime.close()


@pytest.mark.anyio
async def test_barge_in_waits_for_threshold_and_increments_generation_once() -> None:
    bridge = FakeBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=LongPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    reply_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id="turn-1",
                response_id="formal-barge",
                text="長い正式回答です。",
                action="NEXT_QUESTION",
                question_id="q-2",
                state_version=2,
            )
        )
    )
    for _ in range(30):
        if runtime._assistant_speaking:
            break
        await asyncio.sleep(0.01)
    assert runtime._assistant_speaking is True
    generation_before = runtime._generation

    for _ in range(5):
        await runtime.push_audio(_frame(1200))
    assert runtime._generation == generation_before
    assert runtime._turn_active is False
    assert reply_task.done() is False

    await runtime.push_audio(_frame(1200))
    assert runtime._generation == generation_before + 1
    assert runtime._turn_active is True
    assert runtime._voiced_duration_ms == 120

    await asyncio.gather(reply_task, return_exceptions=True)
    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    interrupted = [
        event for event in events if isinstance(event, AssistantInterrupted)
    ]
    assert interrupted[-1].response_id == "formal-barge"
    assert bridge.cancel_calls == []
    assert (
        runtime._assistant_response_states["formal-barge"]
        == AssistantResponseState.INTERRUPTED
    )
    assert not any(
        isinstance(event, AssistantSpeechEnded)
        and event.response_id == "formal-barge"
        for event in events
    )
    await runtime.close()


@pytest.mark.anyio
async def test_new_speech_cancels_only_pending_evaluating_turn() -> None:
    bridge = BlockingBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    process_task = asyncio.create_task(
        runtime._process_interview_turn(
            transcript="最初の回答です",
            generation=1,
            expected_state_version=1,
            client_turn_id="pending-client-turn",
        )
    )
    runtime._processing_task = process_task
    await bridge.process_started.wait()

    await runtime.push_audio(_frame(1200))
    if runtime._state_sync_task is not None:
        await runtime._state_sync_task

    assert bridge.cancel_calls == [
        {
            "voice_session_id": "vs-1",
            "client_turn_id": "pending-client-turn",
            "expected_state_version": 1,
        }
    ]
    assert runtime._turn_active is True
    assert runtime._state_version == 2
    await asyncio.gather(process_task, return_exceptions=True)
    await runtime.close()


@pytest.mark.anyio
async def test_output_interrupt_does_not_cancel_pending_interview_turn() -> None:
    bridge = BlockingBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    process_task = asyncio.create_task(
        runtime._process_interview_turn(
            transcript="評価中の回答です",
            generation=0,
            expected_state_version=1,
            client_turn_id="pending-output-only",
        )
    )
    runtime._processing_task = process_task
    await bridge.process_started.wait()

    await runtime.interrupt()

    assert bridge.cancel_calls == []
    assert process_task.done() is False
    assert runtime._generation == 1

    bridge.release_process.set()
    await process_task
    await runtime.close()


@pytest.mark.anyio
async def test_barge_in_after_commit_keeps_state_and_uses_current_question() -> None:
    bridge = FakeBridge()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=LongPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    committed_reply = InterviewBridgeResult(
        turn_id="turn-committed",
        response_id="response-committed",
        reply_text="次の質問を読み上げています。",
        action="NEXT_QUESTION",
        question_id="q-2",
        state_version=2,
        interview_status="active",
    )
    runtime._apply_bridge_result(committed_reply)
    reply_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id=committed_reply.turn_id,
                response_id=committed_reply.response_id,
                text=committed_reply.reply_text,
                action=committed_reply.action,
                question_id=committed_reply.question_id,
                state_version=committed_reply.state_version,
            )
        )
    )
    for _ in range(30):
        if runtime._assistant_speaking:
            break
        await asyncio.sleep(0.01)

    for _ in range(6):
        await runtime.push_audio(_frame(1200))
    await asyncio.gather(reply_task, return_exceptions=True)

    assert bridge.cancel_calls == []
    assert runtime._state_version == 2
    assert runtime._current_question_id == "q-2"
    await runtime._on_transcribe_result(
        TranscribeResult(
            text="はい",
            stable_text="はい",
            is_partial=False,
            result_id="confirmation-result",
        )
    )
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.11)

    assert bridge.process_calls[-1]["transcript"] == "はい"
    assert bridge.process_calls[-1]["answer_to_question_id"] == "q-2"
    assert bridge.process_calls[-1]["expected_state_version"] == 2
    await runtime.close()


@pytest.mark.anyio
async def test_first_polly_chunk_plays_before_following_chunk_is_ready() -> None:
    polly = StreamingPolly()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    reply_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id="turn-1",
                response_id="response-streaming",
                text="最初の文章です。後続の文章はまだ生成中です。",
                action="NEXT_QUESTION",
                question_id="q-2",
                state_version=2,
            )
        )
    )
    first_audio = None
    for _ in range(50):
        events = list(runtime._events._queue)
        first_audio = next(
            (
                event
                for event in events
                if isinstance(event, AssistantAudioChunk)
                and event.response_id == "response-streaming"
            ),
            None,
        )
        if first_audio is not None:
            break
        await asyncio.sleep(0.01)
    assert first_audio is not None
    assert reply_task.done() is False

    polly.release_following.set()
    await reply_task
    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    assert (
        len(
            [
                event
                for event in events
                if isinstance(event, AssistantSpeechStarted)
                and event.response_id == "response-streaming"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                event
                for event in events
                if isinstance(event, AssistantSpeechEnded)
                and event.response_id == "response-streaming"
            ]
        )
        == 1
    )
    await runtime.close()


@pytest.mark.anyio
async def test_generation_change_cancels_unfinished_polly_tasks() -> None:
    polly = StreamingPolly()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    reply_task = asyncio.create_task(
        runtime.send_reply(
            SimpleNamespace(
                turn_id="turn-1",
                response_id="response-cancel-tasks",
                text="最初の文章です。二番目の文章です。三番目の文章です。",
                action="NEXT_QUESTION",
                question_id="q-2",
                state_version=2,
            )
        )
    )
    for _ in range(50):
        if len(polly.calls) >= 2:
            break
        await asyncio.sleep(0.01)
    await runtime.interrupt()
    await asyncio.gather(reply_task, return_exceptions=True)

    assert polly.following_cancelled is True
    await runtime.close()


@pytest.mark.anyio
async def test_cache_miss_does_not_mark_listen_ack_played() -> None:
    polly = FakePolly()
    polly.cache.pop(LISTEN_ACK_TEXT)
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=polly,
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    runtime._voiced_duration_ms = 1000
    await runtime._maybe_play_listen_ack("設備を担当しています")

    assert runtime._listen_ack_played is False
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("transcript", "normal_should_finalize"),
    [
        ("担当しています。", True),
        ("なので。", False),
    ],
)
async def test_normal_endpoint_normalizes_punctuation_and_suppresses_continuation(
    transcript: str,
    normal_should_finalize: bool,
) -> None:
    bridge = FakeBridge()
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=replace(
            _config(),
            normal_endpoint_ms=50,
            hard_endpoint_ms=200,
        ),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.push_audio(_frame(1200))
    await transcribe.result(transcript, stable_text=transcript, is_partial=False)
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.09)
    assert bool(bridge.process_calls) is normal_should_finalize
    if not normal_should_finalize:
        await asyncio.sleep(0.14)
        assert len(bridge.process_calls) == 1
    await runtime.close()


@pytest.mark.anyio
async def test_partial_stable_transcript_waits_for_final_or_hard_endpoint() -> None:
    bridge = FakeBridge()
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=replace(
            _config(),
            normal_endpoint_ms=50,
            hard_endpoint_ms=180,
            final_result_wait_ms=20,
        ),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.push_audio(_frame(1200))
    await transcribe.result("設備を担当して", stable_text="設備を担当して", is_partial=True)
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.08)

    assert bridge.process_calls == []
    await asyncio.sleep(0.25)
    assert len(bridge.process_calls) == 1
    await runtime.close()


@pytest.mark.anyio
async def test_long_speech_uses_longer_final_endpoint_settle_window() -> None:
    bridge = FakeBridge()
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=replace(
            _config(),
            normal_endpoint_ms=50,
            hard_endpoint_ms=300,
            final_result_settle_ms=0,
            long_form_speech_ms=100,
            long_form_endpoint_ms=180,
        ),
        interview_bridge=bridge,  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    for _ in range(6):
        await runtime.push_audio(_frame(1200))
    await transcribe.result("長い回答です。", is_partial=False)
    await runtime.push_audio(_frame(0))
    await asyncio.sleep(0.08)

    assert bridge.process_calls == []
    await asyncio.sleep(0.14)
    assert len(bridge.process_calls) == 1
    await runtime.close()


@pytest.mark.anyio
async def test_end_interview_disables_new_turn_and_never_returns_to_listening() -> None:
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await runtime.send_reply(
        SimpleNamespace(
            turn_id="turn-end",
            response_id="response-end",
            text="インタビューを終了します。",
            action="END_INTERVIEW",
            question_id=None,
            state_version=2,
        )
    )
    await runtime.notify_assistant_playback_drained(
        response_id="response-end",
        generation=runtime._generation,
    )
    await runtime.push_audio(_frame(1200))

    events = []
    while not runtime._events.empty():
        events.append(runtime._events.get_nowait())
    input_states = [
        event.input_state
        for event in events
        if isinstance(event, InputStateChanged)
    ]
    assert input_states[-1] == "INTERVIEW_COMPLETED"
    assert runtime._turn_active is False
    assert runtime._input_available is False
    await runtime.close()


@pytest.mark.anyio
async def test_transcribe_fatal_disables_audio_and_close_collects_tasks() -> None:
    transcribe = FakeTranscribe()
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=transcribe,
        polly=FakePolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    assert transcribe.on_fatal_error is not None
    await transcribe.on_fatal_error(ConnectionError("closed"))
    sent_before = len(transcribe.audio)
    for _ in range(5):
        await runtime.push_audio(_frame(1200))

    assert len(transcribe.audio) == sent_before
    assert runtime._input_available is False
    await runtime.close()
    assert not runtime._background_tasks
    assert not runtime._notice_tasks


@pytest.mark.anyio
async def test_warm_failure_is_retrieved_and_close_leaves_no_tasks() -> None:
    runtime = TranscribePollyRuntime(
        config=_config(),
        interview_bridge=FakeBridge(),  # type: ignore[arg-type]
        transcribe=FakeTranscribe(),
        polly=WarmFailingPolly(),
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-1",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )
    await asyncio.sleep(0)
    await runtime.close()

    assert not runtime._background_tasks
    assert not runtime._notice_tasks
