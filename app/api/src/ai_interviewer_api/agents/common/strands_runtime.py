from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.models import BedrockModel

from ai_interviewer_api.core.config import settings


_OPENAI_GPT_56_MODEL_MARKERS = (
    "openai.gpt-5.6-terra",
    "openai.gpt-5.6-luna",
)


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


def _supports_bedrock_temperature(model_id: str) -> bool:
    """Return whether the selected Bedrock model accepts temperature.

    The GPT-5.6 Terra/Luna Global inference profiles reject the Converse
    ``temperature`` field.  Keep temperature for other Bedrock models, while
    also recognizing the ARN form of the same Global profiles.
    """
    normalized_model_id = model_id.strip().lower()
    return not any(marker in normalized_model_id for marker in _OPENAI_GPT_56_MODEL_MARKERS)


def create_bedrock_model(
    *,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    boto_client_config: BotocoreConfig | None = None,
) -> BedrockModel:
    resolved_model_id = (model_id or settings.bedrock_model_id).strip()
    model_config: dict[str, Any] = {
        "boto_client_config": boto_client_config,
        "region_name": resolve_bedrock_region(region_name),
        "model_id": resolved_model_id,
        "max_tokens": settings.bedrock_max_tokens if max_tokens is None else max_tokens,
        "streaming": False,
    }
    if _supports_bedrock_temperature(resolved_model_id):
        model_config["temperature"] = settings.bedrock_temperature if temperature is None else temperature

    return BedrockModel(
        **model_config,
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
        ),
    )


@lru_cache(maxsize=4)
def _voice_bedrock_runtime_client(
    region_name: str,
    connect_timeout: float,
    read_timeout: float,
) -> Any:
    """Reuse the low-level client used by the latency-sensitive voice path."""
    return boto3.client(
        "bedrock-runtime",
        region_name=region_name,
        config=BotocoreConfig(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


def invoke_voice_bedrock_text(
    *,
    system_prompt: str,
    prompt: str,
    max_tokens: int,
) -> str:
    """Invoke Bedrock Converse without Strands' tool-based structured output.

    Voice decisions are still validated by the caller. This path avoids the
    extra tool-use round trip that is unnecessary for compact, enum-oriented
    responses.
    """
    region_name = resolve_bedrock_region()
    client = _voice_bedrock_runtime_client(
        region_name,
        settings.voice_bedrock_connect_timeout_seconds,
        settings.voice_bedrock_read_timeout_seconds,
    )
    request: dict[str, Any] = {
        "modelId": settings.voice_bedrock_model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "temperature": settings.voice_bedrock_temperature,
            "maxTokens": max_tokens,
        },
    }
    if system_prompt.strip():
        request["system"] = [{"text": system_prompt}]
    response = client.converse(**request)
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("text")
    ).strip()
    if not text:
        raise ValueError("voice Bedrock text response missing")
    return text


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
