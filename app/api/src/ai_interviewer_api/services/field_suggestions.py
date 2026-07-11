from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest, KnowledgeFieldCreate
from ai_interviewer_api.services.prompts.loader import (
    get_field_fill_system_prompt,
    get_json_repair_system_prompt,
)

logger = logging.getLogger(__name__)

ALLOWED_INPUT_TYPES = {
    "short_text",
    "long_text",
    "number",
    "date",
    "single_select",
    "multi_select",
    "checklist",
    "related_entity",
}


@dataclass(frozen=True)
class ParsedFieldSuggestions:
    reply: str
    fields: list[KnowledgeFieldCreate]


def suggest_fields_with_bedrock(payload: FieldSuggestionRequest, user: UserContext) -> dict:
    if not settings.bedrock_enabled:
        raise HTTPException(status_code=503, detail="bedrock_disabled")

    model_id = payload.context.defaultModelId or settings.bedrock_model_id
    if not payload.content.strip():
        response = {
            "reply": "まだ作りたい質問テーマが見えていません。どんな場面のナレッジを整理したいかを一言で教えてください。",
            "fields": [],
            "modelId": model_id,
            "bedrockInvoked": False,
        }
        _debug_log(
            "final_response",
            content=payload.content,
            recent_messages_count=len(payload.recentMessages),
            fields_length=0,
            bedrock_invoked=False,
        )
        return response
    _debug_log(
        "request",
        content=payload.content,
        recent_messages_count=len(payload.recentMessages),
        recent_messages_tail=_summarize_recent_messages(payload.recentMessages[-3:]),
        existing_fields_count=len(payload.existingFields),
        model_id=model_id,
    )
    repair_attempted = False
    try:
        text = _invoke_bedrock_model(model_id, payload, user)
        try:
            suggestions = _parse_suggestions(text, payload.maxFields)
            _debug_log(
                "parsed_response",
                repair_attempted=repair_attempted,
                reply=suggestions.reply,
                fields_length=len(suggestions.fields),
            )
        except Exception:
            logger.warning("Retrying field suggestions JSON repair")
            repair_attempted = True
            _debug_log("json_repair", repair_attempted=True)
            repaired_text = _repair_json_with_bedrock(model_id, text)
            suggestions = _parse_suggestions(repaired_text, payload.maxFields)
            _debug_log(
                "parsed_response",
                repair_attempted=repair_attempted,
                reply=suggestions.reply,
                fields_length=len(suggestions.fields),
            )
    except HTTPException:
        raise
    except (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError) as exc:
        logger.warning("Bedrock field suggestion request timed out or failed to connect: %s", exc)
        raise HTTPException(status_code=504, detail="bedrock_unreachable") from exc
    except ClientError as exc:
        logger.warning("Bedrock field suggestion request failed with client error: %s", exc)
        raise _map_bedrock_client_error(exc) from exc
    except BotoCoreError as exc:
        logger.warning("Bedrock field suggestion request failed with botocore error: %s", exc)
        raise HTTPException(status_code=503, detail="bedrock_connection_error") from exc
    except Exception as exc:
        logger.exception("Failed to generate field suggestions with Amazon Bedrock")
        raise HTTPException(status_code=500, detail="field_suggestion_generation_failed") from exc

    existing_names = {field.name.strip() for field in payload.existingFields if field.name.strip()}
    suggested_fields = [
        field.model_dump()
        for field in suggestions.fields
        if field.name.strip() and field.name.strip() not in existing_names
    ]

    reply = suggestions.reply.strip()
    if not suggested_fields and not reply:
        reply = "既存項目は維持しつつ、追加で深掘りしたい観点があれば教えてください。"

    response = {
        "reply": reply,
        "fields": suggested_fields,
        "modelId": model_id,
        "bedrockInvoked": True,
    }
    _debug_log(
        "final_response",
        content=payload.content,
        recent_messages_count=len(payload.recentMessages),
        parsed_fields_length=len(suggestions.fields),
        final_fields_length=len(suggested_fields),
        bedrock_invoked=True,
    )
    return response


def _invoke_bedrock_model(model_id: str, payload: FieldSuggestionRequest, user: UserContext) -> str:
    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    prompt_text = _build_effective_system_prompt(payload)
    user_payload = {
        "task": "Suggest knowledge field definitions for an AI interview knowledge DB.",
        "tenantId": user.tenant_id,
        "userRequest": payload.content,
        "knowledgeDbContext": payload.context.model_dump(),
        "recentConversation": _serialize_recent_messages(payload),
        "existingFields": [field.model_dump() for field in payload.existingFields],
        "constraints": {
            "maxFields": payload.maxFields,
            "allowedInputTypes": sorted(ALLOWED_INPUT_TYPES),
            "schema": {
                "reply": (
                    "string: User-facing natural Japanese reply. "
                    "If the request is still vague, ask exactly one concise follow-up question and keep fields empty. "
                    "If the user already provided a concrete equipment / trigger / issue / desired knowledge combination, return fields in the same response. "
                    "Do not continue meta-questioning once the request is specific enough to propose question items. "
                    "Do not put numbered lists, bullet lists, or candidate question items in reply. "
                    "If you want to propose items, put every candidate only in fields."
                ),
                "fields": [
                    {
                        "name": "string: proposed interview field name",
                        "description": "string: what this field should capture from the expert",
                        "inputType": "short_text|long_text|number|date|single_select|multi_select|checklist|related_entity",
                        "required": "boolean",
                        "askByAi": "boolean",
                        "aiQuestionExamples": ["string: a concrete question the AI interviewer can ask"],
                        "options": ["string"],
                    }
                ],
            },
        },
    }
    _debug_log(
        "bedrock_request",
        model_id=model_id,
        prompt_hash=_hash_text(prompt_text),
        prompt_preview=_prompt_preview(prompt_text),
        user_payload_summary={
            "userRequest": payload.content,
            "recentConversationCount": len(user_payload["recentConversation"]),
            "existingFieldsCount": len(user_payload["existingFields"]),
            "maxFields": payload.maxFields,
            "context": {
                "name": payload.context.name,
                "category": payload.context.category,
                "targetBusiness": payload.context.targetBusiness,
                "targetEquipment": payload.context.targetEquipment,
                "language": payload.context.language,
                "defaultModelId": payload.context.defaultModelId,
            },
        },
    )
    response = client.converse(
        modelId=model_id,
        system=[{"text": prompt_text}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": json.dumps(user_payload, ensure_ascii=False)
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": settings.bedrock_max_tokens,
            "temperature": settings.bedrock_temperature,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(part["text"] for part in content if "text" in part).strip()
    _debug_log("bedrock_raw_text", raw_text=text)
    return text


def _repair_json_with_bedrock(model_id: str, raw_text: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    response = client.converse(
        modelId=model_id,
        system=[{"text": get_json_repair_system_prompt()}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": json.dumps(
                            {
                                "brokenResponse": raw_text,
                                "requiredShape": {
                                    "reply": "string",
                                    "fields": [
                                        {
                                            "name": "string",
                                            "description": "string",
                                            "inputType": "short_text|long_text|number|date|single_select|multi_select|checklist|related_entity",
                                            "required": "boolean",
                                            "askByAi": "boolean",
                                            "aiQuestionExamples": ["string"],
                                            "options": ["string"],
                                        }
                                    ],
                                },
                            },
                            ensure_ascii=False,
                        )
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": settings.bedrock_max_tokens,
            "temperature": 0,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(part["text"] for part in content if "text" in part).strip()


def _serialize_recent_messages(payload: FieldSuggestionRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for message in payload.recentMessages[-20:]:
        content = message.content.strip()
        if not content:
            continue
        role = "assistant" if message.role in {"ai", "assistant"} else "user"
        messages.append({"role": role, "content": content})
    return messages


def _build_effective_system_prompt(payload: FieldSuggestionRequest) -> str:
    return get_field_fill_system_prompt()


def _build_fallback_reply(payload: FieldSuggestionRequest) -> str:
    recent_assistant_messages = [
        message.content.strip()
        for message in payload.recentMessages[-6:]
        if message.role in {"ai", "assistant"} and message.content.strip()
    ]
    if recent_assistant_messages:
        return (
            "続けて整理したいので、"
            "どの業務や場面の質問を作りたいかを一言で教えてください。"
        )
    return (
        "こんにちは。"
        "どの業務や場面の質問を作りたいかを一言で教えてください。"
    )


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


def _parse_suggestions(raw_text: str, max_fields: int) -> ParsedFieldSuggestions:
    data = json.loads(_extract_json(raw_text))
    raw_fields = data.get("fields", []) if isinstance(data, dict) else data
    if not isinstance(raw_fields, list):
        raise ValueError("field suggestions response must contain a fields array")
    raw_reply = str(data.get("reply") or "").strip() if isinstance(data, dict) else ""

    fields: list[KnowledgeFieldCreate] = []
    seen_names: set[str] = set()
    for item in raw_fields[: max(1, min(max_fields, 20))]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        input_type = str(item.get("inputType") or "long_text")
        if input_type not in ALLOWED_INPUT_TYPES:
            input_type = "long_text"
        fields.append(
            KnowledgeFieldCreate(
                name=name,
                description=str(item.get("description") or "").strip() or None,
                inputType=input_type,
                required=bool(item.get("required", False)),
                askByAi=bool(item.get("askByAi", True)),
                aiQuestionExamples=_string_list(item.get("aiQuestionExamples")),
                options=_string_list(item.get("options")),
                displayOrder=len(fields) + 1,
            )
        )

    if not fields and raw_reply:
        return ParsedFieldSuggestions(reply=raw_reply, fields=[])

    if not fields:
        raise ValueError("field suggestions response did not include valid fields")
    return ParsedFieldSuggestions(reply=raw_reply or _fallback_reply(fields), fields=fields)


def _extract_json(raw_text: str) -> str:
    text = raw_text.strip()
    if not text:
        raise ValueError("field suggestions response was empty")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        return text[object_start : object_end + 1]
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        return text[array_start : array_end + 1]
    return text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _fallback_reply(fields: list[KnowledgeFieldCreate]) -> str:
    field_names = "、".join(field.name for field in fields[:4])
    return f"{field_names}を中心に、聞き取りから承認判断までつながる項目案を作成しました。"


def _debug_log(event: str, **payload: Any) -> None:
    if settings.app_env.lower() not in {"local", "dev", "development"}:
        return
    logger.info("field_suggestions.%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))


def _summarize_recent_messages(messages: list[Any]) -> list[dict[str, str]]:
    summarized: list[dict[str, str]] = []
    for message in messages:
        content = getattr(message, "content", "")
        role = getattr(message, "role", "")
        summarized.append(
            {
                "role": str(role),
                "content": str(content).strip()[:160],
            }
        )
    return summarized


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _prompt_preview(text: str) -> list[str]:
    return [line.strip()[:160] for line in text.splitlines()[:3]]
