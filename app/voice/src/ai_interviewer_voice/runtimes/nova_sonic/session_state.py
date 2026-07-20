"""
Role:
    Nova Sonic Runtime内部で共有する状態モデル定義。

Summary:
    completion、tool turn、voice session、観測ログに関するdataclassとEnumをまとめ、
    runtime本体と補助コンポーネントが同じ状態構造を参照できるようにする。

Relations:
    Used by nova_sonic.runtime and extracted runtime helper components.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseAuthorizationState
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


class CompletionStatus(str, Enum):
    GENERATING = "generating"
    OUTPUT_COMPLETE = "output_complete"
    PROTOCOL_COMPLETE = "protocol_complete"


class InterviewTurnKind(str, Enum):
    INITIAL = "initial"
    USER_ANSWER = "user_answer"


class InputState(str, Enum):
    ASSISTANT_SPEAKING = "ASSISTANT_SPEAKING"
    ANSWER_LISTENING = "ANSWER_LISTENING"
    ANSWER_PROCESSING = "ANSWER_PROCESSING"
    CONFIRMATION_LISTENING = "CONFIRMATION_LISTENING"


@dataclass
class CompletionState:
    completion_id: str
    started_at_ms: int | None = None
    response_id: str | None = None
    generation: int | None = None
    authorized: bool = False
    user_transcript_received: bool = False
    assistant_speculative_text_received: bool = False
    assistant_audio_chunks: int = 0
    assistant_final_text_received: bool = False
    assistant_audio_end_received: bool = False
    assistant_final_text_end_received: bool = False
    completion_end_received: bool = False
    stop_reason: str | None = None
    status: CompletionStatus = CompletionStatus.GENERATING
    planned_reply_text: str | None = None
    spoken_transcript: str = ""
    finalized: bool = False


@dataclass
class VoiceTurnTrace:
    trace_id: str
    turn_index: int
    user_speech_started_at: int | None = None
    user_speech_ended_at: int | None = None
    user_transcript_final_at: int | None = None
    tool_use_received_at: int | None = None
    tool_content_end_received_at: int | None = None
    turn_save_started_at: int | None = None
    turn_saved_at: int | None = None
    interview_process_started_at: int | None = None
    interview_process_completed_at: int | None = None
    tool_result_content_start_sent_at: int | None = None
    tool_result_sent_at: int | None = None
    tool_result_content_end_sent_at: int | None = None
    assistant_text_first_received_at: int | None = None
    assistant_audio_first_received_at: int | None = None
    assistant_speech_started_event_sent_at: int | None = None
    assistant_transcript_final_at: int | None = None
    assistant_speech_ended_at: int | None = None
    turn_id: str | None = None
    response_id: str | None = None
    completion_id: str | None = None
    tool_use_id: str | None = None


@dataclass
class PendingToolCall:
    completion_id: str
    kind: InterviewTurnKind = InterviewTurnKind.USER_ANSWER
    processing_mode: str = "unknown"
    tool_content_id: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    user_transcript: str | None = None
    tool_use_received: bool = False
    tool_content_end_received: bool = False
    tool_content_stop_reason: str | None = None
    result_sent: bool = False
    interview_task: asyncio.Task[InterviewBridgeResult] | None = None
    trace: VoiceTurnTrace | None = None
    initial_reply_text: str | None = None
    initial_question_id: str | None = None
    evaluation_result: InterviewBridgeResult | None = None
    local_preface_response_id: str | None = None
    local_preface_generation: int | None = None
    local_preface_playback_drained: bool = False
    evaluation_tool_result_dispatching: bool = False
    preface_sent: bool = False
    evaluation_reply_sent: bool = False
    evaluation_reply_dispatching: bool = False
    evaluation_reply_send_attempts: int = 0
    evaluation_audio_first_chunk_logged: bool = False
    confirmation_preface_enqueued_at_ms: int | None = None
    confirmation_preface_output_complete_at_ms: int | None = None
    evaluation_reply_ready_at_ms: int | None = None
    evaluation_reply_send_started_at_ms: int | None = None
    evaluation_reply_send_completed_at_ms: int | None = None
    evaluation_reply_response_id: str | None = None
    evaluation_retry_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class ApprovedToolResponse:
    response_id: str
    completion_id: str
    tool_use_id: str
    turn_id: str
    planned_reply_text: str
    action: str
    question_id: str | None
    state_version: int
    tool_result_sent_at_ms: int
    retrieval_policy: str | None = None
    retrieval_executed: bool = False


@dataclass
class VoiceSessionRuntimeState:
    voice_session_id: str
    record_id: str
    owner_user_id: str | None = None
    current_question_id: str | None = None
    state_version: int = 0
    interview_status: str = "active"


@dataclass
class ContentState:
    content_id: str
    completion_id: str | None = None
    role: str | None = None
    content_type: str | None = None
    generation_stage: str = "unknown"


@dataclass
class NovaSonicObservedOutput:
    last_input_event: str | None = None
    received_event_types: list[str] = field(default_factory=list)
    text_output_received: bool = False
    user_transcript_received: bool = False
    user_transcript_text_length: int = 0
    assistant_text_output_received: bool = False
    assistant_final_text_received: bool = False
    audio_output_chunks: int = 0
    assistant_audio_bytes: int = 0
    completion_start_received: bool = False
    assistant_content_start_received: bool = False
    completion_end_received: bool = False
    completion_stop_reason: str | None = None
    completion_wait_timeout: bool = False
    content_end_received: bool = False
    silence_continued_during_completion_wait: bool = False
    silence_frames_during_completion_wait: int = 0
    unknown_event_count: int = 0
    unknown_event_keys: list[str] = field(default_factory=list)
    explicit_stream_error: bool = False
    model_stream_error: bool = False
    completion_status: str = CompletionStatus.GENERATING.value
    completion_protocol_degraded: bool = False
    response_authorization_state: str = ResponseAuthorizationState.BLOCKED.value
    failed_stage: str = "none"
    last_event_type: str | None = None
    last_content_id: str | None = None
    user_speech_start_ms: int | None = None
    user_speech_end_ms: int | None = None
    completion_start_ms: int | None = None
    user_final_text_end_ms: int | None = None
    assistant_speculative_text_end_ms: int | None = None
    assistant_audio_start_ms: int | None = None
    assistant_audio_end_ms: int | None = None
    assistant_final_text_start_ms: int | None = None
    assistant_final_text_end_ms: int | None = None
    completion_end_ms: int | None = None
    audio_content_end_sent_at_ms: int | None = None
    prompt_end_sent_at_ms: int | None = None
    session_end_sent_at_ms: int | None = None
    completion_end_received_at_ms: int | None = None
    completion_end_after_session_end: bool = False
    session_protocol_complete: bool = False
    session_close_degraded: bool = False
    unauthorized_completion_count: int = 0
    unauthorized_audio_chunks: int = 0
    unauthorized_audio_bytes: int = 0
    unauthorized_text_received: bool = False
    spontaneous_completion_started: bool = False
    approved_reply_sent: bool = False
    approved_reply_sent_at_ms: int | None = None
    approved_completion_started: bool = False
    approved_completion_id: str | None = None
    approved_output_complete: bool = False
    approved_protocol_complete: bool = False
    planned_reply_text: str | None = None
    planned_reply_length: int = 0
    spoken_transcript: str = ""
    spoken_transcript_length: int = 0
    spoken_matches_exactly: bool = False
    spoken_contains_planned_reply: bool = False
    tool_use_received: bool = False
    tool_name: str | None = None
    tool_use_id_present: bool = False
    tool_use_completion_matches: bool = False
    tool_output_content_end_received: bool = False
    tool_output_stop_reason: str | None = None
    tool_result_content_start_sent: bool = False
    tool_result_sent: bool = False
    tool_result_content_end_sent: bool = False
    tool_result_sent_after_tool_content_end: bool = False
    tool_result_delay_ms: int = 0
    explicit_stream_error_type: str | None = None
    explicit_stream_error_message: str | None = None
    last_sent_event: str | None = None
    last_sent_content_name: str | None = None
    last_received_event: str | None = None
    tool_use_received_at_ms: int | None = None
    tool_content_end_received_at_ms: int | None = None
    tool_result_content_start_sent_at_ms: int | None = None
    tool_result_sent_at_ms: int | None = None
    tool_result_content_end_sent_at_ms: int | None = None
    turn_saved: bool = False
    turn_id_present: bool = False
    interview_process_called: bool = False
    interview_process_completed: bool = False
    reply_text_present: bool = False
    pre_tool_assistant_text_count: int = 0
    pre_tool_audio_chunks: int = 0
    pre_tool_audio_bytes: int = 0
    post_tool_assistant_text_received: bool = False
    post_tool_audio_chunks: int = 0
    ignored_user_speech_during_tool_wait_count: int = 0
