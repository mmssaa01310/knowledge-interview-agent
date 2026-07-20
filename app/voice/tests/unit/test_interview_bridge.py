import asyncio

import pytest

from ai_interviewer_voice.clients.interview_api import (
    InterviewApiError,
    VoiceSessionSnapshot,
    VoiceTurnProcessResult,
    VoiceTurnSaveResult,
)
from ai_interviewer_voice.services.interview_bridge import (
    InterviewBridge,
    InvalidInterviewResponseError,
)


class FakeInterviewApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.save_result = VoiceTurnSaveResult(turn_id="turn-1", processing_status="pending")
        self.process_result = VoiceTurnProcessResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="確認します。",
            action="ask_followup",
            question_id="q-2",
            state_version=3,
            interview_status="active",
        )

    async def get_voice_session(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> VoiceSessionSnapshot:
        self.calls.append(("get_voice_session", (voice_session_id,), {"timeout_seconds": timeout_seconds}))
        return VoiceSessionSnapshot(
            voice_session_id=voice_session_id,
            record_id="record-1",
            owner_user_id="user-1",
            current_question_id="q-1",
            state_version=1,
            interview_status="active",
        )

    async def save_turn(self, voice_session_id: str, **kwargs) -> VoiceTurnSaveResult:
        self.calls.append(("save_turn", (voice_session_id,), kwargs))
        return self.save_result

    async def process_turn(self, voice_session_id: str, turn_id: str, **kwargs) -> VoiceTurnProcessResult:
        self.calls.append(("process_turn", (voice_session_id, turn_id), kwargs))
        return self.process_result

    async def create_assistant_event(self, voice_session_id: str, **kwargs) -> None:
        self.calls.append(("create_assistant_event", (voice_session_id,), kwargs))


def test_interview_bridge_saves_then_processes_turn() -> None:
    async def run() -> tuple[object, list[tuple[str, tuple, dict]]]:
        client = FakeInterviewApiClient()
        bridge = InterviewBridge(client)
        result = await bridge.process_turn(
            voice_session_id="session-1",
            transcript="回答です",
            answer_to_question_id="q-1",
        )
        return result, client.calls

    result, calls = asyncio.run(run())
    assert [call[0] for call in calls[:2]] == ["save_turn", "process_turn"]
    assert calls[0][2]["answer_to_question_id"] == "q-1"
    assert result.reply_text == "確認します。"
    assert result.question_id == "q-2"


def test_interview_bridge_rejects_invalid_process_response() -> None:
    async def run() -> None:
        client = FakeInterviewApiClient()
        client.process_result = VoiceTurnProcessResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="   ",
            action="ask_followup",
            question_id=None,
            state_version=3,
            interview_status="active",
        )
        bridge = InterviewBridge(client)

        with pytest.raises(InvalidInterviewResponseError):
            await bridge.process_turn(
                voice_session_id="session-1",
                transcript="回答です",
                answer_to_question_id="q-1",
            )

    asyncio.run(run())


def test_interview_bridge_exposes_api_errors() -> None:
    async def run() -> None:
        class FailingClient(FakeInterviewApiClient):
            async def save_turn(self, voice_session_id: str, **kwargs) -> VoiceTurnSaveResult:
                raise InterviewApiError("turn_save_failed", "save failed")

        bridge = InterviewBridge(FailingClient())

        with pytest.raises(InterviewApiError) as exc_info:
            await bridge.process_turn(
                voice_session_id="session-1",
                transcript="回答です",
                answer_to_question_id="q-1",
            )

        assert exc_info.value.code == "turn_save_failed"

    asyncio.run(run())
