from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
VOICE_SOURCE_ROOT = REPOSITORY_ROOT / "app" / "voice" / "src"
if str(VOICE_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(VOICE_SOURCE_ROOT))


def _install_transcribe_import_boundary() -> None:
    """Load the real Runtime in API's test env without importing the AWS SDK."""

    module_name = "ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream"
    if module_name in sys.modules:
        return
    module = ModuleType(module_name)

    @dataclass(frozen=True)
    class TranscribeResult:
        text: str
        stable_text: str
        is_partial: bool
        result_id: str | None = None
        confidence: float | None = None

    class AwsTranscribeStreamingPort:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("Inject the deterministic Transcribe port in this test")

    class TranscribeStreamingPort:
        pass

    module.TranscribeResult = TranscribeResult  # type: ignore[attr-defined]
    module.AwsTranscribeStreamingPort = AwsTranscribeStreamingPort  # type: ignore[attr-defined]
    module.TranscribeStreamingPort = TranscribeStreamingPort  # type: ignore[attr-defined]
    sys.modules[module_name] = module


_install_transcribe_import_boundary()

from ai_interviewer_api.agents.interview_knowledge import service as structured_service  # noqa: E402
from ai_interviewer_api.agents.interview_knowledge.schemas import (  # noqa: E402
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext  # noqa: E402
from ai_interviewer_api.core.config import settings as api_settings  # noqa: E402
from ai_interviewer_api.models.interview_plan import InterviewPlan  # noqa: E402
from ai_interviewer_api.repositories.store import store  # noqa: E402
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db  # noqa: E402
from ai_interviewer_api.routers.knowledge_fields import create_field  # noqa: E402
from ai_interviewer_api.routers.knowledges import create_knowledge  # noqa: E402
from ai_interviewer_api.routers.records import create_record  # noqa: E402
from ai_interviewer_api.schemas.requests import (  # noqa: E402
    KnowledgeCreate,
    KnowledgeDbCreate,
    KnowledgeFieldCreate,
    RecordCreate,
)
from ai_interviewer_api.schemas.voice import VoiceSessionCreate  # noqa: E402
from ai_interviewer_api.services.conversation_policy import (  # noqa: E402
    BedrockCanonicalIntentProvider,
    CanonicalIntentOutput,
)
from ai_interviewer_api.services.interview_state_transition import (  # noqa: E402
    apply_background_state_proposal,
)
from ai_interviewer_api.services.voice_interview import create_voice_session  # noqa: E402
from ai_interviewer_api.main import app as api_app  # noqa: E402

from ai_interviewer_voice.clients.interview_api import InterviewApiClient  # noqa: E402
from ai_interviewer_voice.runtimes.transcribe_polly.config import (  # noqa: E402
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import (  # noqa: E402
    TranscribePollyRuntime,
)
from ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream import (  # noqa: E402
    TranscribeResult,
)
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext  # noqa: E402
from ai_interviewer_voice.services.interview_bridge import InterviewBridge  # noqa: E402


PROFILE_QUESTION = "お名前、所属部署、現在の役職または担当領域を教えてください。"
PROFILE_ITEMS = [
    {"itemId": "name", "label": "お名前"},
    {"itemId": "department", "label": "所属部署"},
    {"itemId": "role_or_responsibility", "label": "役職または担当領域"},
]
PROFILE_FIELD_UPDATES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "llm_responses"
        / "profile_field_updates.json"
    ).read_text(encoding="utf-8")
)


def async_test(function: Any) -> Any:
    """Run async integration scenarios without adding a pytest plugin."""

    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


class DeterministicInterviewProvider:
    """Fake only the external LLM boundary; state and transition code stays real."""

    def __init__(self, *, block_background: bool = False) -> None:
        from threading import Event

        self.background_started = Event()
        self.release_background = Event()
        self.block_background = block_background
        self.interpret_calls: list[dict[str, Any]] = []

    def generate_question(
        self,
        *,
        target: dict[str, Any],
        context: dict[str, Any],
        **_: Any,
    ) -> QuestionGenerationOutput:
        missing = target.get("missingItems")
        if isinstance(missing, list) and missing:
            labels = [
                str(item.get("label") or "")
                for item in missing
                if isinstance(item, dict) and item.get("label")
            ]
            if labels:
                return QuestionGenerationOutput(
                    questionText=f"{', '.join(labels)}について教えてください。"
                )
        return QuestionGenerationOutput(questionText=PROFILE_QUESTION)

    def interpret(self, *, context: dict[str, Any], **_: Any) -> StructuredInterviewOutput:
        import threading

        self.interpret_calls.append(context)
        if self.block_background and threading.current_thread().name.startswith(
            "structured-background-validation"
        ):
            self.background_started.set()
            self.release_background.wait(timeout=5)

        latest = context.get("latestUtterance")
        latest = latest if isinstance(latest, dict) else {}
        raw = str(latest.get("rawTranscript") or "").strip()
        message_id = str(latest.get("messageId") or "")
        fields = context.get("fields")
        fields = fields if isinstance(fields, list) else []
        profile_field = next(
            (
                item
                for item in fields
                if isinstance(item, dict) and item.get("name") == "基本プロフィール"
            ),
            None,
        )
        field_id = str((profile_field or {}).get("id") or "")
        update: FieldUpdate | None = None
        update_spec = PROFILE_FIELD_UPDATES.get(raw)
        if field_id and isinstance(update_spec, dict):
            update = FieldUpdate(
                fieldId=field_id,
                itemId=update_spec.get("itemId"),
                value=str(update_spec.get("value") or ""),
                evidenceTranscriptIds=[message_id],
                answerResolution=update_spec.get("answerResolution"),
            )
        return StructuredInterviewOutput(
            dialogueAct="ANSWER",
            transcriptAssessment=TranscriptAssessment(
                rawTranscript=raw,
                normalizedTranscript=raw,
            ),
            answerAssessment=AnswerAssessment(sufficiency="PARTIAL"),
            fieldUpdates=[update] if update is not None else [],
        )


class DeterministicTranscribe:
    def __init__(self) -> None:
        self._on_result: Any = None
        self.audio_chunks: list[bytes] = []

    async def start(self, *, on_result: Any, on_reconnecting: Any, on_fatal_error: Any) -> None:
        self._on_result = on_result

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_chunks.append(pcm)

    async def close(self) -> None:
        return None

    async def emit_final(self, text: str, result_id: str) -> None:
        if self._on_result is None:
            raise AssertionError("Transcribe Runtime has not started")
        await self._on_result(
            TranscribeResult(
                text=text,
                stable_text=text,
                is_partial=False,
                result_id=result_id,
                confidence=0.99,
            )
        )


class DeterministicPolly:
    async def warm(self, _: tuple[str, ...]) -> None:
        return None

    async def get_cached(self, _: str) -> bytes | None:
        return None

    async def synthesize(self, _: str) -> bytes:
        # Empty PCM drives the real Runtime completion/drain path without AWS I/O.
        return b""


@dataclass
class VoiceScenario:
    user: UserContext
    record: dict[str, Any]
    knowledge: dict[str, Any]
    field: dict[str, Any]
    voice_session: dict[str, Any]
    structured_provider: DeterministicInterviewProvider
    transcribe: DeterministicTranscribe
    runtime: TranscribePollyRuntime
    http_client: httpx.AsyncClient

    async def close(self) -> None:
        await self.runtime.close()
        await self.http_client.aclose()

    async def submit_transcript_turn(self, text: str, result_id: str) -> None:
        await self.runtime._start_user_turn(increment_generation=True, initial_voiced_ms=100)
        await self.transcribe.emit_final(text, result_id)
        await self.runtime._finalize_user_turn(text)
        task = self.runtime._processing_task
        if task is None:
            raise AssertionError("Runtime did not dispatch the finalized transcript")
        await task


def install_deterministic_interview_provider(
    monkeypatch: Any,
    *,
    intent_by_text: dict[str, str] | None = None,
    block_background: bool = False,
) -> DeterministicInterviewProvider:
    provider = DeterministicInterviewProvider(block_background=block_background)
    monkeypatch.setattr(
        structured_service,
        "_get_structured_provider",
        lambda *_args, **_kwargs: provider,
    )
    intents = intent_by_text or {}

    def classify_intent(
        _self: Any,
        *,
        context: dict[str, Any],
    ) -> CanonicalIntentOutput:
        utterance = str(context.get("latestUserUtterance") or "")
        return CanonicalIntentOutput(
            dialogueAct=intents.get(utterance, "ANSWER")  # type: ignore[arg-type]
        )

    monkeypatch.setattr(BedrockCanonicalIntentProvider, "classify", classify_intent)
    return provider


def create_profile_voice_session(
    *,
    user: UserContext | None = None,
    extra_required_field: bool = False,
) -> tuple[UserContext, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    user = user or DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="E2E voice test"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="人物インタビュー",
            purpose="基本プロフィールを確認する",
            interviewPlan=InterviewPlan(
                profile="fixed_form",
                modelId="global.openai.gpt-5.6-terra",
            ),
        ),
        user,
    )
    field = create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="基本プロフィール",
            description="名前、所属部署、役職または担当領域を取得する",
            inputType="long_text",
            required=True,
            retrievalPolicy="never",
            aiQuestionExamples=[PROFILE_QUESTION],
            questionPlan={"purpose": "基本プロフィール", "requiredItems": PROFILE_ITEMS},
            displayOrder=1,
        ),
        user,
    )
    if extra_required_field:
        create_field(
            knowledge["id"],
            KnowledgeFieldCreate(
                name="補足業務",
                description="追加の必須確認項目",
                inputType="long_text",
                required=True,
                retrievalPolicy="never",
                aiQuestionExamples=["補足業務について教えてください。"],
                displayOrder=2,
            ),
            user,
        )
    record = create_record(
        knowledge["id"],
        RecordCreate(title="E2E人物インタビュー"),
        user,
    )
    voice_session = create_voice_session(record["id"], VoiceSessionCreate(), user)
    return user, record, knowledge, field, voice_session


def seed_fast_path_regression_state(
    *,
    record: dict[str, Any],
    field: dict[str, Any],
    voice_session: dict[str, Any],
    state_version: int = 7,
    question_id: str = "q-002",
) -> dict[str, Any]:
    """Set the actual persisted state to the historical q-002 regression point."""

    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    if state is None:
        raise AssertionError("Voice-session creation did not persist Interview State")
    prior_question = next(
        (
            item
            for item in state.get("askedQuestions", [])
            if item.get("questionId") == state.get("currentQuestionId")
        ),
        None,
    )
    if prior_question is None:
        raise AssertionError("Initial current question was not persisted")
    current_question = deepcopy(prior_question)
    current_question.update(
        {
            "questionId": question_id,
            "fieldId": field["id"],
            "targetType": "field",
            "targetId": field["id"],
            "targetLabel": field["name"],
            "text": PROFILE_QUESTION,
        }
    )
    if not any(
        item.get("questionId") == question_id
        for item in state.get("askedQuestions", [])
    ):
        state.setdefault("askedQuestions", []).append(current_question)
    state["currentQuestionId"] = question_id
    state["currentFieldId"] = field["id"]
    state["stateVersion"] = state_version
    state["analysisVersion"] = 0
    field_state = state.setdefault("fieldStates", {}).setdefault(str(field["id"]), {})
    field_state.update(
        {
            "answerState": "UNANSWERED",
            "answerResolution": None,
            "status": "pending",
            "candidateAnswer": None,
            "recordAnswer": None,
            "candidateItems": [],
            "capturedItems": [],
            "confirmedItems": [],
            "capturedItemIds": [],
            "missingRequiredItemIds": [item["itemId"] for item in PROFILE_ITEMS],
        }
    )
    state["clarificationQueue"] = []
    store.upsert("interview_states", state)

    session = store.get("voice_sessions", str(voice_session["id"]))
    if session is None:
        raise AssertionError("Voice session disappeared before the Runtime started")
    session["currentQuestionId"] = question_id
    session["stateVersion"] = state_version
    store.upsert("voice_sessions", session)
    return current_question


def persisted_voice_turn(user: UserContext, client_turn_id: str) -> dict[str, Any] | None:
    return next(
        (
            turn
            for turn in store.list("voice_turns", user.tenant_id)
            if turn.get("clientTurnId") == client_turn_id
        ),
        None,
    )


def persisted_user_message(user: UserContext, turn_id: str) -> dict[str, Any]:
    message = next(
        (
            item
            for item in store.list("messages", user.tenant_id)
            if item.get("voiceTurnId") == turn_id and item.get("role") == "user"
        ),
        None,
    )
    if message is None:
        raise AssertionError(f"No persisted user message for VoiceTurn {turn_id}")
    return message


def apply_background_proposal(
    *,
    scenario: VoiceScenario,
    source_question: dict[str, Any],
    client_turn_id: str,
    clarification: bool = False,
) -> Any:
    """Run real background state validation/merge with only the model stubbed."""

    turn = persisted_voice_turn(scenario.user, f"transcribe-{client_turn_id}")
    if turn is None:
        raise AssertionError(f"Runtime did not persist {client_turn_id}")
    message = persisted_user_message(scenario.user, str(turn["id"]))
    output = scenario.structured_provider.interpret(
        context={
            "latestUtterance": {
                "rawTranscript": turn["transcript"],
                "messageId": message["id"],
            },
            "fields": [scenario.field],
        }
    )
    state = store.get("interview_states", f"interview-state-{scenario.record['id']}")
    if state is None:
        raise AssertionError("Interview State disappeared before Background merge")
    proposal: dict[str, Any] = {
        "sourceTurnId": turn["id"],
        "sourceMessageId": message["id"],
        "sourceTurnSequence": turn["sequence"],
        "baseStateVersion": turn["expectedStateVersion"],
        "baseAnalysisVersion": state.get("analysisVersion", 0),
        "sourceQuestion": deepcopy(source_question),
        "rawTranscript": turn["transcript"],
        "structuredOutput": output.model_dump(),
        "validEvidenceIds": [message["id"]],
    }
    if clarification:
        proposal["clarificationProposal"] = {
            "requestId": f"clarification-{client_turn_id}",
            "sourceTurnId": turn["id"],
            "sourceMessageId": message["id"],
            "sourceTarget": source_question,
            "reason": "Background clarification proposal for regression coverage",
            "priority": 1,
        }
    fields = structured_service._list_interview_fields(scenario.knowledge, scenario.user)
    from ai_interviewer_api.agents.interview_knowledge.coordinator import resolve_profile

    return apply_background_state_proposal(
        record_id=scenario.record["id"],
        user=scenario.user,
        proposal=proposal,
        fields=fields,
        profile=resolve_profile(scenario.knowledge),
        source_question=source_question,
    )


async def start_runtime_scenario(
    *,
    user: UserContext,
    record: dict[str, Any],
    knowledge: dict[str, Any],
    field: dict[str, Any],
    voice_session: dict[str, Any],
    structured_provider: DeterministicInterviewProvider,
) -> VoiceScenario:
    transport = httpx.ASGITransport(app=api_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://kikiori.test")
    headers = {"x-internal-api-token": api_settings.internal_api_token}
    initial_sent = await http_client.post(
        f"/internal/voice-sessions/{voice_session['id']}/initial-reply-sent",
        headers=headers,
    )
    if initial_sent.status_code != 200:
        await http_client.aclose()
        raise AssertionError(f"Unable to mark initial voice response sent: {initial_sent.status_code}")

    api_client = InterviewApiClient(
        "http://kikiori.test",
        api_settings.internal_api_token,
        http_client=http_client,
    )
    bridge = InterviewBridge(api_client, turn_process_timeout_seconds=15.0)
    transcribe = DeterministicTranscribe()
    config = TranscribePollyRuntimeConfig(
        provider_name="transcribe_polly",
        transcribe_chunk_ms=20,
        normal_endpoint_ms=0,
        hard_endpoint_ms=0,
        final_result_wait_ms=0,
        final_result_settle_ms=0,
        backchannel_enabled=False,
        polly_max_parallel_requests=1,
    )
    runtime = TranscribePollyRuntime(
        config=config,
        interview_bridge=bridge,
        transcribe=transcribe,  # type: ignore[arg-type]
        polly=DeterministicPolly(),  # type: ignore[arg-type]
    )
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id=str(voice_session["id"]),
            record_id=str(record["id"]),
            provider="transcribe_polly",
        )
    )
    return VoiceScenario(
        user=user,
        record=record,
        knowledge=knowledge,
        field=field,
        voice_session=voice_session,
        structured_provider=structured_provider,
        transcribe=transcribe,
        runtime=runtime,
        http_client=http_client,
    )
