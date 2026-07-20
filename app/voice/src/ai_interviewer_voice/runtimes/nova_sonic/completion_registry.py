"""
Role:
    Nova Sonicのcompletion / content状態の保管と再利用制御。

Summary:
    contentId と completionId の対応、文字起こしバッファ、
    completionId再利用時の状態リセットを一箇所で管理する。

Relations:
    Uses session_state models.
    Used by nova_sonic.runtime and protocol event dispatch paths.
"""

from __future__ import annotations

from ai_interviewer_voice.runtimes.nova_sonic.session_state import CompletionState, CompletionStatus, ContentState


class CompletionRegistry:
    def __init__(self) -> None:
        self.active_output_content_id: str | None = None
        self.active_completion_id: str | None = None
        self.transcript_buffers: dict[str, list[str]] = {}
        self.content_roles: dict[str, str] = {}
        self.content_states: dict[str, ContentState] = {}
        self.completion_states: dict[str, CompletionState] = {}

    def reset(self) -> None:
        self.active_output_content_id = None
        self.active_completion_id = None
        self.transcript_buffers.clear()
        self.content_roles.clear()
        self.content_states.clear()
        self.completion_states.clear()

    def resolve_content_state(self, content_id: str | None) -> ContentState | None:
        if content_id is None:
            return None
        return self.content_states.get(content_id)

    def bind_content(
        self,
        *,
        content_id: str,
        completion_id: str | None,
        role: str | None,
        content_type: str | None,
        generation_stage: str,
    ) -> ContentState:
        state = ContentState(
            content_id=content_id,
            completion_id=completion_id,
            role=role,
            content_type=content_type,
            generation_stage=generation_stage,
        )
        self.content_states[content_id] = state
        if role is not None:
            self.content_roles[content_id] = role
        return state

    def resolve_completion_state(self, completion_id: str | None) -> CompletionState | None:
        if completion_id is None:
            return None
        return self.completion_states.setdefault(
            completion_id,
            CompletionState(completion_id=completion_id),
        )

    def lookup_completion_state(self, completion_id: str | None) -> CompletionState | None:
        if completion_id is None:
            return None
        return self.completion_states.get(completion_id)

    def bind_completion_response(
        self,
        *,
        completion_id: str,
        response_id: str | None,
        generation: int | None,
        started_at_ms: int | None = None,
    ) -> CompletionState:
        state = self.resolve_completion_state(completion_id)
        assert state is not None
        if state.response_id != response_id or state.generation != generation:
            self.reset_completion_output_state(state)
        state.response_id = response_id
        state.generation = generation
        if started_at_ms is not None:
            state.started_at_ms = started_at_ms
        return state

    def reset_completion_output_state(self, state: CompletionState) -> None:
        state.assistant_audio_chunks = 0
        state.assistant_final_text_received = False
        state.assistant_audio_end_received = False
        state.assistant_final_text_end_received = False
        state.completion_end_received = False
        state.stop_reason = None
        state.status = CompletionStatus.GENERATING
        state.spoken_transcript = ""
        state.finalized = False

    def append_transcript(self, content_id: str, text: str) -> None:
        self.transcript_buffers.setdefault(content_id, []).append(text)

    def get_content_role(self, content_id: str | None) -> str | None:
        if content_id is None:
            return None
        return self.content_roles.get(content_id)

    def set_active_output_content_id(self, content_id: str | None) -> None:
        self.active_output_content_id = content_id

    def set_active_completion_id(self, completion_id: str | None) -> None:
        self.active_completion_id = completion_id

    def clear_active_output_content_id(self) -> None:
        self.active_output_content_id = None

    def clear_active_completion_id(self) -> None:
        self.active_completion_id = None

    def pop_transcript(self, content_id: str | None) -> str | None:
        if content_id is None or content_id not in self.transcript_buffers:
            return None
        return "".join(self.transcript_buffers.pop(content_id))

    def remove_completion_content(self, completion_id: str) -> list[str]:
        content_ids = [
            content_id
            for content_id, content_state in self.content_states.items()
            if content_state.completion_id == completion_id
        ]
        for content_id in content_ids:
            self.content_states.pop(content_id, None)
            self.content_roles.pop(content_id, None)
            self.transcript_buffers.pop(content_id, None)
        if self.active_completion_id == completion_id:
            self.active_completion_id = None
        if self.active_output_content_id in content_ids:
            self.active_output_content_id = None
        return content_ids
