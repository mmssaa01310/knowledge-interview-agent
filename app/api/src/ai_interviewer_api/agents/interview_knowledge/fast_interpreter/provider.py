from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProviderError,
    _retry_max_output_tokens,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.core.config import settings


class FastInterpreterProvider(Protocol):
    def assess(self, *, context: Mapping[str, Any]) -> FastAnswerAssessment: ...


class BedrockFastInterpreterProvider:
    """Bedrock adapter for the deliberately small provisional assessment."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        session: Any | None = None,
        http_client_factory: Any | None = None,
    ) -> None:
        self._provider = BedrockResponsesStructuredProvider(
            model_id=model_id or settings.structured_interview_fast_model_id,
            region_name=region_name,
            endpoint_url=endpoint_url,
            session=session,
            http_client_factory=http_client_factory,
        )

    def assess(self, *, context: Mapping[str, Any]) -> FastAnswerAssessment:
        error: Exception | None = None
        max_output_tokens = settings.structured_interview_fast_max_output_tokens
        for attempt in range(2):
            try:
                payload = self._provider.request_structured_output(
                    schema_name="fast_answer_assessment",
                    schema=FastAnswerAssessment.model_json_schema(),
                    system_prompt=_fast_interpreter_system_prompt(),
                    user_payload=context,
                    reasoning_effort=settings.structured_interview_fast_reasoning_effort,
                    max_output_tokens=max_output_tokens,
                )
                return FastAnswerAssessment.model_validate(payload)
            except (StructuredInterviewProviderError, ValueError) as exc:
                error = exc
                if attempt == 0:
                    max_output_tokens = _retry_max_output_tokens(max_output_tokens)
        raise StructuredInterviewProviderError(
            "Fast Interview output validation failed after retry"
        ) from error


def _fast_interpreter_system_prompt() -> str:
    return """あなたは音声インタビューの高速一次回答判定器です。

目的は回答を詳細評価することではありません。現在の質問と最新回答を中心に、次の質問へ一旦進んでもよいかだけを判定してください。

判定するのは次の4点だけです。
1. 現在質問に対する最低限の情報が含まれているか
2. 回答が意味的に理解可能か
3. 明らかに発話途中ではないか
4. 最新発話が回答ではなく、現在の質問の意味・対象範囲・答え方を尋ねているか

少し情報不足でも、現在質問への回答として意味が通るならminimumInformationPresent=trueを優先してください。理由、具体例、判断基準、例外、詳細情報、他項目、過去回答との整合性、Process、矛盾、Transcript correctionは評価・抽出しないでください。

「はい」だけ、質問とほぼ無関係な発話、意味不明な発話はminimumInformationPresentまたはunderstandableをfalseにしてください。明らかな文途中はclearlyIncomplete=trueにしてください。

ユーザーが「何を聞いているのか」「どの範囲か」「何を答えればよいか」など、現在質問の意味や回答方法を尋ねている場合だけneedsQuestionExplanation=trueにしてください。この場合は回答不足ではなく、現在質問の説明を返す必要があります。通常の回答、ためらい、回答できないという発話ではfalseにしてください。

返却は指定されたJSON Schemaだけに従い、reasonは短いデバッグ用説明にしてください。""".strip()
