from __future__ import annotations

import logging

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi import HTTPException

from ai_interviewer_api.agents.question_design.adapter import (
    adapt_question_design_output,
    build_question_design_input,
)
from ai_interviewer_api.agents.question_design.service import run_question_design
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest

logger = logging.getLogger(__name__)

_SAFE_REPLY = "一時的に質問項目を生成できませんでした。少し時間をおいて再度お試しください。"


def suggest_fields_with_bedrock(payload: FieldSuggestionRequest, user: UserContext) -> dict:
    if not settings.bedrock_enabled:
        raise HTTPException(status_code=503, detail="bedrock_disabled")

    model_id = payload.context.defaultModelId or settings.bedrock_model_id
    if not payload.content.strip():
        return {
            "reply": "まだ作りたい質問テーマが見えていません。どんな場面のナレッジを整理したいかを一言で教えてください。",
            "fields": [],
            "modelId": model_id,
            "bedrockInvoked": False,
        }

    try:
        output = run_question_design(
            build_question_design_input(payload),
            model_id=model_id,
        )
    except (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError) as exc:
        logger.warning("Strands question design request timed out or failed to connect: %s", exc)
        raise HTTPException(status_code=504, detail="bedrock_unreachable") from exc
    except ClientError as exc:
        logger.warning("Strands question design request failed with client error: %s", exc)
        raise _map_bedrock_client_error(exc) from exc
    except BotoCoreError as exc:
        logger.warning("Strands question design request failed with botocore error: %s", exc)
        raise HTTPException(status_code=503, detail="bedrock_connection_error") from exc
    except Exception:
        logger.exception("Failed to generate field suggestions with Strands question design agent")
        return {
            "reply": _SAFE_REPLY,
            "fields": [],
            "modelId": model_id,
            "bedrockInvoked": True,
        }

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

    return {
        "reply": reply,
        "fields": suggested_fields,
        "modelId": model_id,
        "bedrockInvoked": True,
    }


def _map_bedrock_client_error(exc: ClientError) -> HTTPException:
    error = exc.response.get("Error", {})
    code = str(error.get("Code") or "")
    transient_codes = {
        "InternalServerException",
        "ModelNotReadyException",
        "ModelTimeoutException",
        "ServiceUnavailableException",
        "ThrottlingException",
        "TooManyRequestsException",
    }
    if code in transient_codes:
        return HTTPException(status_code=503, detail=f"bedrock_{code}")
    return HTTPException(status_code=502, detail=f"bedrock_{code or 'client_error'}")
