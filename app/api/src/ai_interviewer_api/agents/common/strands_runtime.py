from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.models import BedrockModel

from ai_interviewer_api.core.config import settings


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def resolve_bedrock_region(region_name: str | None = None) -> str:
    resolved_region = _normalize_optional(region_name)
    if resolved_region:
        return resolved_region

    configured_region = _normalize_optional(settings.bedrock_aws_region)
    if configured_region:
        return configured_region

    return (
        _normalize_optional(os.getenv("BEDROCK_AWS_REGION"))
        or _normalize_optional(os.getenv("AWS_REGION"))
        or _normalize_optional(os.getenv("AWS_DEFAULT_REGION"))
        or "ap-northeast-1"
    )


def create_bedrock_model(
    *,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    boto_client_config: BotocoreConfig | None = None,
) -> BedrockModel:
    return BedrockModel(
        boto_client_config=boto_client_config,
        region_name=resolve_bedrock_region(region_name),
        model_id=model_id or settings.bedrock_model_id,
        temperature=settings.bedrock_temperature if temperature is None else temperature,
        max_tokens=settings.bedrock_max_tokens if max_tokens is None else max_tokens,
        streaming=False,
    )


def create_voice_evaluation_bedrock_model() -> BedrockModel:
    return create_bedrock_model(
        model_id=settings.voice_bedrock_model_id,
        temperature=settings.voice_bedrock_temperature,
        max_tokens=settings.voice_bedrock_max_tokens,
        boto_client_config=BotocoreConfig(
            connect_timeout=settings.voice_bedrock_connect_timeout_seconds,
            read_timeout=settings.voice_bedrock_read_timeout_seconds,
            retries={"total_max_attempts": 1, "mode": "standard"},
        )
    )


def create_agent(
    *,
    model: BedrockModel | None = None,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
    hooks: list[Callable[..., Any]] | None = None,
    trace_attributes: Mapping[str, str | bool | float | int | list[str] | list[bool] | list[float] | list[int]] | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Agent:
    return Agent(
        model=model or create_bedrock_model(),
        system_prompt=system_prompt,
        tools=tools or [],
        hooks=hooks or [],
        callback_handler=None,
        trace_attributes=trace_attributes,
        name=name,
        description=description,
    )
