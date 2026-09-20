from __future__ import annotations

from collections.abc import Mapping

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db
from ai_interviewer_api.routers.knowledge_fields import create_field
from ai_interviewer_api.routers.knowledges import create_knowledge
from ai_interviewer_api.routers.records import create_record
from ai_interviewer_api.schemas.requests import (
    KnowledgeCreate,
    KnowledgeDbCreate,
    KnowledgeFieldCreate,
    RecordCreate,
)


class FakeStructuredProvider:
    def __init__(self) -> None:
        self.interpret_calls: list[dict[str, object]] = []
        self.question_calls: list[dict[str, object]] = []

    def generate_question(
        self,
        *,
        target: Mapping[str, object],
        context: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_calls.append({"target": dict(target), "context": dict(context)})
        if target.get("probeType"):
            return QuestionGenerationOutput(
                questionText="大きな転機でなくても、印象に残っている出来事はありますか？"
            )
        if context.get("interviewLocale") == "en-US":
            return QuestionGenerationOutput(
                questionText=f"Please tell me about {target.get('label') or 'that'}."
            )
        return QuestionGenerationOutput(
            questionText=f"{target.get('label') or 'その点'}について教えてください。"
        )

    def interpret(
        self,
        *,
        context: Mapping[str, object],
        **_: object,
    ) -> StructuredInterviewOutput:
        self.interpret_calls.append(dict(context))
        latest = context.get("latestUtterance")
        latest = latest if isinstance(latest, Mapping) else {}
        raw = str(latest.get("rawTranscript") or "").strip()
        message_id = str(latest.get("messageId") or "")
        assessment = TranscriptAssessment(
            rawTranscript=raw,
            normalizedTranscript=raw,
            correctionStatus="NONE",
        )
        if raw.endswith(("担当し", "関わっ")):
            return StructuredInterviewOutput(
                utteranceCompleteness="INCOMPLETE",
                transcriptAssessment=assessment,
                answerAssessment=AnswerAssessment(sufficiency="INCOMPLETE"),
            )
        compact_raw = raw.strip(" 、。！？!?.,")
        if compact_raw in {"特にありません", "特にない"} or "あまり覚えていません" in raw:
            return StructuredInterviewOutput(
                transcriptAssessment=assessment,
                answerAssessment=AnswerAssessment(
                    sufficiency="REFUSAL",
                    probeType="REFRAME",
                ),
            )

        question = context.get("currentQuestion")
        question = question if isinstance(question, Mapping) else {}
        fields = context.get("fields")
        fields = fields if isinstance(fields, list) else []
        updates: list[FieldUpdate] = []
        if "山田太郎" in raw:
            values = {
                "氏名": "山田太郎",
                "部署": "開発部",
                "役職": "主任",
                "担当領域": "社内システムの設計と開発",
                "担当": "開発部の主任",
            }
            for field in fields:
                if not isinstance(field, Mapping):
                    continue
                field_id = str(field.get("id") or "")
                label = str(field.get("name") or "")
                if field_id and label in values:
                    updates.append(
                        FieldUpdate(
                            fieldId=field_id,
                            value=values[label],
                            evidenceTranscriptIds=[message_id],
                            answerResolution="AUTO_CONFIRM",
                        )
                    )
        if not updates:
            target_id = str(question.get("targetId") or "")
            if target_id:
                updates.append(
                    FieldUpdate(
                        fieldId=target_id,
                        value=raw.removesuffix("です。").removesuffix("です").strip(" 、。"),
                        evidenceTranscriptIds=[message_id],
                        answerResolution="AUTO_CONFIRM",
                    )
                )
        return StructuredInterviewOutput(
            transcriptAssessment=assessment,
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=updates,
        )


def _create_record_with_fields(user: UserContext, fields: list[tuple[str, str]]) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="音声インタビュー",
            targetEquipment="圧入機A",
            interviewPlan=InterviewPlan(
                profile="fixed_form",
                modelId="global.openai.gpt-5.6-terra",
            ),
        ),
        user,
    )
    for index, (name, input_type) in enumerate(fields, start=1):
        create_field(
            knowledge["id"],
            KnowledgeFieldCreate(
                name=name,
                inputType=input_type,
                required=True,
                askByAi=True,
                retrievalPolicy="never",
                aiQuestionExamples=[f"{name}を教えてください。"],
                displayOrder=index,
            ),
            user,
        )
    return create_record(
        knowledge["id"],
        RecordCreate(title="音声インタビュー"),
        user,
    )


def _create_record_with_field(user: UserContext, *, interview_locale: str | None = None) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="音声インタビュー",
            targetEquipment="圧入機A",
            interviewPlan=InterviewPlan(profile="fixed_form", modelId="global.openai.gpt-5.6-terra"),
        ),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["どのような現象が起きていますか？"],
            displayOrder=1,
        ),
        user,
    )
    return create_record(
        knowledge["id"],
        RecordCreate(title="朝一の荷重ばらつき", interviewLocale=interview_locale),
        user,
    )


def _set_pending_voice_field_candidate(record_id: str, candidate: str) -> None:
    state = store.get("interview_states", f"interview-state-{record_id}")
    assert state is not None
    question_id = state["currentQuestionId"]
    question = next(
        item for item in state["askedQuestions"] if item["questionId"] == question_id
    )
    field_id = question["fieldId"]
    field_state = state["fieldStates"][field_id]
    required_items = field_state["questionPlan"]["requiredItems"]
    candidate_items = [
        {
            "itemId": item["itemId"],
            "value": candidate,
            "evidenceTranscriptIds": ["candidate-message"],
        }
        for item in required_items
    ]
    field_state.update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "answerResolution": "CONFIRM_REQUIRED",
            "status": "asking",
            "candidateAnswer": candidate,
            "candidateSource": "user_statement",
            "candidateItems": candidate_items,
            "capturedItems": candidate_items,
            "capturedItemIds": [item["itemId"] for item in candidate_items],
            "missingRequiredItemIds": [],
        }
    )
    state["lastTentativeTarget"] = {"targetType": "field", "targetId": field_id}
    store.upsert("interview_states", state)
