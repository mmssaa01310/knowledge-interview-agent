from __future__ import annotations

import logging

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.provider import StructuredInterviewProviderError
from ai_interviewer_api.agents.question_design.adapter import (
    adapt_question_design_output,
    build_question_design_input,
)
from ai_interviewer_api.agents.question_design.schemas import PriorKnowledgeContext
from ai_interviewer_api.agents.question_design.service import (
    QuestionDesignInternalError,
    run_question_design,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import DEFAULT_QUESTION_DESIGN_MODEL_ID, settings
from ai_interviewer_api.models.interview_plan import STRUCTURED_INTERVIEW_MODEL_IDS
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest
from ai_interviewer_api.services.prior_knowledge import build_prior_knowledge_context
from ai_interviewer_api.services.question_design_retrieval import retrieve_question_design_context

logger = logging.getLogger(__name__)


def suggest_fields_with_openai(
    payload: FieldSuggestionRequest,
    user: UserContext,
    *,
    knowledge_id: str | None = None,
) -> dict:
    model_id = _resolve_question_design_model_id(payload.context.defaultModelId)
    if not payload.content.strip():
        return {
            "reply": "まだ作りたい質問テーマが見えていません。どんな場面のナレッジを整理したいかを一言で教えてください。",
            "fields": [],
            "modelId": model_id,
        }

    retrieved_context = (
        retrieve_question_design_context(payload, knowledge_id=knowledge_id, user=user)
        if knowledge_id
        else []
    )
    prior_knowledge = (
        [
            PriorKnowledgeContext.model_validate(item)
            for item in build_prior_knowledge_context({"id": knowledge_id}, user)
        ]
        if knowledge_id
        else []
    )
    question_input = build_question_design_input(
        payload,
        knowledge_id=knowledge_id,
        prior_knowledge=prior_knowledge,
        retrieved_context=retrieved_context,
    )
    logger.info(
        "question_design_context_retrieved knowledge_id=%s sources=%s",
        knowledge_id,
        len(retrieved_context),
    )

    try:
        output = run_question_design(question_input, model_id=model_id)
    except StructuredInterviewProviderError as exc:
        logger.error("Question design OpenAI Responses request failed: %s", exc)
        raise HTTPException(status_code=502, detail="question_design_provider_error") from exc
    except QuestionDesignInternalError as exc:
        logger.error(
            "question_design_internal_error code=%s model_id=%s",
            exc.code,
            model_id,
        )
        raise HTTPException(status_code=502, detail=exc.code) from exc
    except Exception as exc:
        logger.exception("Failed to generate field suggestions with OpenAI Responses")
        raise HTTPException(status_code=500, detail="question_design_internal_error") from exc

    adapted = adapt_question_design_output(output)
    existing_names = {field.name.strip() for field in payload.existingFields if field.name.strip()}
    suggested_fields = [
        field.model_dump()
        for field in adapted.fields
        if field.name.strip() and field.name.strip() not in existing_names
    ]
    reply = adapted.reply.strip()
    if not suggested_fields and not reply:
        reply = "既存項目は維持しつつ、追加で深掘りしたい観点があれば教えてください。"

    response = {
        "reply": reply,
        "fields": suggested_fields,
        "modelId": model_id,
    }
    if retrieved_context:
        response["retrievedSources"] = [
            {
                "sourceType": item.source_type,
                "sourceId": item.source_id,
                "title": item.title,
                "score": item.score,
            }
            for item in retrieved_context
        ]
    if adapted.interview_plan is not None:
        response["interviewPlan"] = adapted.interview_plan.model_dump()
    return response


def _resolve_question_design_model_id(requested_model_id: str | None) -> str:
    """Allow only direct OpenAI models exposed by the question-design UI."""
    requested = requested_model_id.strip() if isinstance(requested_model_id, str) else ""
    if requested in STRUCTURED_INTERVIEW_MODEL_IDS:
        return requested

    configured = settings.question_design_model_id.strip()
    if configured in STRUCTURED_INTERVIEW_MODEL_IDS:
        return configured
    return DEFAULT_QUESTION_DESIGN_MODEL_ID
