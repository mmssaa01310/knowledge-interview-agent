from __future__ import annotations

from dataclasses import dataclass

from ai_interviewer_voice.clients.interview_api import (
    InitialReplyClaimResult,
    InterviewApiClient,
    InterviewApiError,
    VoiceSessionSnapshot,
    VoiceTurnProcessResult,
    VoiceTurnSaveResult,
)


@dataclass(frozen=True)
class InterviewBridgeResult:
    turn_id: str
    response_id: str
    reply_text: str
    action: str
    question_id: str | None
    state_version: int
    interview_status: str
    retrieval_policy: str | None = None
    retrieval_executed: bool = False


class InvalidInterviewResponseError(RuntimeError):
    pass


class InterviewBridge:
    def __init__(
        self,
        client: InterviewApiClient,
        *,
        turn_save_timeout_seconds: float = 5.0,
        turn_process_timeout_seconds: float = 5.0,
    ) -> None:
        self._client = client
        self._turn_save_timeout_seconds = turn_save_timeout_seconds
        self._turn_process_timeout_seconds = turn_process_timeout_seconds

    async def load_voice_session(
        self,
        voice_session_id: str,
    ) -> VoiceSessionSnapshot:
        return await self._client.get_voice_session(
            voice_session_id,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def claim_initial_reply(
        self,
        voice_session_id: str,
    ) -> InitialReplyClaimResult:
        return await self._client.claim_initial_reply(
            voice_session_id,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def mark_initial_reply_sent(
        self,
        voice_session_id: str,
    ) -> None:
        await self._client.mark_initial_reply_sent(
            voice_session_id,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def mark_initial_reply_failed(
        self,
        voice_session_id: str,
    ) -> None:
        await self._client.mark_initial_reply_failed(
            voice_session_id,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def process_turn(
        self,
        *,
        voice_session_id: str,
        transcript: str,
        answer_to_question_id: str | None,
        turn_type: str = "ANSWER",
        expected_state_version: int | None = None,
        client_turn_id: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
    ) -> InterviewBridgeResult:
        save_result = await self.save_turn(
            voice_session_id,
            transcript=transcript,
            answer_to_question_id=answer_to_question_id,
            turn_type=turn_type,
            expected_state_version=expected_state_version,
            client_turn_id=client_turn_id,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
        )
        return await self.process_saved_turn(
            voice_session_id=voice_session_id,
            turn_id=save_result.turn_id,
        )

    async def save_turn(
        self,
        voice_session_id: str,
        *,
        transcript: str,
        answer_to_question_id: str | None,
        turn_type: str = "ANSWER",
        expected_state_version: int | None = None,
        client_turn_id: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
    ) -> VoiceTurnSaveResult:
        return await self._client.save_turn(
            voice_session_id,
            transcript=transcript,
            answer_to_question_id=answer_to_question_id,
            turn_type=turn_type,
            expected_state_version=expected_state_version,
            client_turn_id=client_turn_id,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def cancel_turn(
        self,
        *,
        voice_session_id: str,
        client_turn_id: str,
        expected_state_version: int,
    ) -> None:
        await self._client.cancel_turn(
            voice_session_id,
            client_turn_id=client_turn_id,
            expected_state_version=expected_state_version,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    async def process_saved_turn(
        self,
        *,
        voice_session_id: str,
        turn_id: str,
    ) -> InterviewBridgeResult:
        process_result = await self._client.process_turn(
            voice_session_id,
            turn_id,
            timeout_seconds=self._turn_process_timeout_seconds,
        )
        return self._validate_process_result(process_result)

    async def create_assistant_event(
        self,
        *,
        voice_session_id: str,
        event_type: str,
        response_id: str | None,
        generation: int | None,
        transcript: str | None,
        detail: dict,
    ) -> None:
        await self._client.create_assistant_event(
            voice_session_id,
            event_type=event_type,
            response_id=response_id,
            generation=generation,
            transcript=transcript,
            detail=detail,
            timeout_seconds=self._turn_save_timeout_seconds,
        )

    def _validate_process_result(
        self,
        result: VoiceTurnProcessResult,
    ) -> InterviewBridgeResult:
        if not result.turn_id or not result.response_id or not result.reply_text.strip():
            raise InvalidInterviewResponseError("missing required interview response fields")
        return InterviewBridgeResult(
            turn_id=result.turn_id,
            response_id=result.response_id,
            reply_text=result.reply_text.strip(),
            action=result.action.strip() or "ask_configured_field",
            question_id=result.question_id,
            state_version=result.state_version,
            interview_status=result.interview_status,
            retrieval_policy=result.retrieval_policy,
            retrieval_executed=result.retrieval_executed,
        )


__all__ = [
    "InterviewApiError",
    "InterviewBridge",
    "InterviewBridgeResult",
    "InvalidInterviewResponseError",
]
