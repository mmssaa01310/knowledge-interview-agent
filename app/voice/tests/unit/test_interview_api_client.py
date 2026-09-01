import asyncio
import json

import httpx
import pytest

from ai_interviewer_voice.clients.interview_api import InterviewApiClient, InterviewApiError


def test_interview_api_client_gets_session_and_processes_turn() -> None:
    async def run() -> tuple[object, object, object, list[tuple[str, str, dict | None]]]:
        calls: list[tuple[str, str, dict | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else None
            calls.append((request.method, request.url.path, body))
            if request.url.path == "/internal/voice-sessions/session-1":
                return httpx.Response(
                    200,
                    json={
                        "id": "session-1",
                        "recordId": "record-1",
                        "ownerUserId": "user-1",
                        "currentQuestionId": "q-1",
                        "stateVersion": 2,
                        "status": "active",
                        "interviewLocale": "pt-BR",
                    },
                )
            if request.url.path == "/internal/voice-sessions/session-1/turns":
                return httpx.Response(200, json={"id": "turn-1", "processingStatus": "pending"})
            if request.url.path == "/internal/voice-sessions/session-1/turn-intent":
                return httpx.Response(200, json={"turnType": "ANSWER"})
            if request.url.path == "/internal/voice-sessions/session-1/turns/turn-1/process":
                return httpx.Response(
                    200,
                    json={
                        "turnId": "turn-1",
                        "responseId": "resp-1",
                        "text": "確認します。",
                        "action": "ask_followup",
                        "questionId": "q-2",
                        "stateVersion": 3,
                        "voiceSession": {"status": "active"},
                    },
                )
            raise AssertionError(request.url.path)

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
        )

        session = await client.get_voice_session("session-1")
        saved = await client.save_turn(
            "session-1",
            transcript="回答",
            answer_to_question_id="q-1",
            stt_confidence=0.91,
        )
        processed = await client.process_turn("session-1", "turn-1")
        return session, saved, processed, calls

    session, saved, processed, calls = asyncio.run(run())
    assert session.current_question_id == "q-1"
    assert session.interview_locale == "pt-BR"
    assert saved.turn_id == "turn-1"
    assert processed.reply_text == "確認します。"
    assert processed.question_id == "q-2"
    assert calls[1][2]["answerToQuestionId"] == "q-1"
    assert calls[1][2]["sttConfidence"] == 0.91


def test_interview_api_client_classifies_turn_intent() -> None:
    async def run() -> tuple[str, dict]:
        calls: list[tuple[str, str, dict | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else None
            calls.append((request.method, request.url.path, body))
            return httpx.Response(200, json={"turnType": "CONTROL"})

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="http://test",
            ),
        )
        result = await client.classify_voice_turn_intent(
            "session-1",
            transcript="会話を終了してください",
            answer_to_question_id="q-1",
            expected_state_version=2,
        )
        return result.turn_type, calls[0][2] or {}

    turn_type, body = asyncio.run(run())
    assert turn_type == "CONTROL"
    assert body["answerToQuestionId"] == "q-1"
    assert body["expectedStateVersion"] == 2


def test_interview_api_client_maps_unauthorized() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "invalid"})

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
        )

        with pytest.raises(InterviewApiError) as exc_info:
            await client.get_voice_session("session-1")

        assert exc_info.value.code == "unauthorized"

    asyncio.run(run())


def test_interview_api_client_classifies_process_timeout() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("process timed out", request=request)

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
        )

        with pytest.raises(InterviewApiError) as exc_info:
            await client.process_turn("session-1", "turn-1", timeout_seconds=30)

        assert exc_info.value.code == "turn_process_failed_timeout"
        assert exc_info.value.category == "PROCESS_TIMEOUT"

    asyncio.run(run())


def test_interview_api_client_classifies_process_api_error() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "internal error"})

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
        )

        with pytest.raises(InterviewApiError) as exc_info:
            await client.process_turn("session-1", "turn-1", timeout_seconds=30)

        assert exc_info.value.code == "turn_process_failed_api_error"
        assert exc_info.value.category == "API_ERROR"

    asyncio.run(run())


def test_interview_api_client_classifies_process_network_error() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = InterviewApiClient(
            "http://test",
            "internal-token",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
        )

        with pytest.raises(InterviewApiError) as exc_info:
            await client.process_turn("session-1", "turn-1", timeout_seconds=30)

        assert exc_info.value.code == "turn_process_failed_network_error"
        assert exc_info.value.category == "NETWORK_ERROR"

    asyncio.run(run())
