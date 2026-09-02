from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    ProcessModelEditOutput,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
)
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    interview_language_instruction,
    normalize_interview_locale,
)


class StructuredInterviewProviderError(RuntimeError):
    """Raised when the configured structured-output provider cannot respond."""


logger = logging.getLogger(__name__)


class StructuredInterviewProvider(Protocol):
    def interpret(
        self,
        *,
        profile: str,
        context: Mapping[str, Any],
        reasoning_effort: str,
    ) -> StructuredInterviewOutput: ...

    def generate_question(
        self,
        *,
        profile: str,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        reasoning_effort: str,
    ) -> QuestionGenerationOutput: ...


class ProcessModelEditProvider(Protocol):
    def edit_process_model(
        self,
        *,
        context: Mapping[str, Any],
        reasoning_effort: str,
    ) -> ProcessModelEditOutput: ...


class BedrockResponsesStructuredProvider:
    """Amazon Bedrock OpenAI-compatible Responses API adapter.

    GPT-5.6 Terra or Luna is invoked through the Bedrock Runtime endpoint with
    an inference profile model ID. AWS credentials are used for SigV4
    signing; the adapter does not require an OpenAI or Bedrock API key.

    This adapter owns provider-specific HTTP details. Interview state,
    priority decisions, and output validation remain in the backend
    coordinator.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        session: Any | None = None,
        http_client_factory: Any | None = None,
    ) -> None:
        self.model_id = (model_id or settings.structured_interview_model_id).strip()
        self.region_name = (region_name or settings.bedrock_aws_region).strip()
        self.endpoint_url = (
            endpoint_url
            or f"https://bedrock-runtime.{self.region_name}.amazonaws.com/openai/v1"
        ).rstrip("/")
        self.session = session or boto3.Session(region_name=self.region_name)
        self.http_client_factory = http_client_factory or httpx.Client
        self.timeout = httpx.Timeout(
            timeout=settings.structured_interview_read_timeout_seconds,
            connect=settings.structured_interview_connect_timeout_seconds,
        )

    def interpret(
        self,
        *,
        profile: str,
        context: Mapping[str, Any],
        reasoning_effort: str,
    ) -> StructuredInterviewOutput:
        error: Exception | None = None
        max_output_tokens = settings.structured_interview_max_output_tokens
        for attempt in range(2):
            try:
                payload = self._request(
                    model=self.model_id,
                    reasoning_effort=reasoning_effort,
                    schema_name="structured_interview_output",
                    schema=StructuredInterviewOutput.model_json_schema(),
                    system_prompt=_interpreter_system_prompt(
                        profile,
                        normalize_interview_locale(context.get("interviewLocale")) or "ja-JP",
                    ),
                    user_payload=context,
                    max_output_tokens=max_output_tokens,
                )
                return StructuredInterviewOutput.model_validate(payload)
            except (StructuredInterviewProviderError, ValueError) as exc:
                error = exc
                if attempt == 0:
                    max_output_tokens = _retry_max_output_tokens(max_output_tokens)
        raise StructuredInterviewProviderError("Structured Interview output validation failed after retry") from error

    def generate_question(
        self,
        *,
        profile: str,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        reasoning_effort: str,
    ) -> QuestionGenerationOutput:
        error: Exception | None = None
        for _ in range(2):
            try:
                payload = self._request(
                    model=self.model_id,
                    reasoning_effort=reasoning_effort,
                    schema_name="interview_question",
                    schema=QuestionGenerationOutput.model_json_schema(),
                    system_prompt=_question_system_prompt(
                        profile,
                        normalize_interview_locale(context.get("interviewLocale")) or "ja-JP",
                    ),
                    user_payload={"context": context, "target": target},
                    max_output_tokens=settings.structured_interview_question_max_output_tokens,
                )
                output = QuestionGenerationOutput.model_validate(payload)
                if not output.questionText.strip():
                    raise StructuredInterviewProviderError("structured question text is empty")
                return output
            except (StructuredInterviewProviderError, ValueError) as exc:
                error = exc
        raise StructuredInterviewProviderError("question output validation failed after retry") from error

    def edit_process_model(
        self,
        *,
        context: Mapping[str, Any],
        reasoning_effort: str,
    ) -> ProcessModelEditOutput:
        error: Exception | None = None
        max_output_tokens = settings.structured_interview_max_output_tokens
        for attempt in range(2):
            try:
                payload = self._request(
                    model=self.model_id,
                    reasoning_effort=reasoning_effort,
                    schema_name="process_model_edit_output",
                    schema=ProcessModelEditOutput.model_json_schema(),
                    system_prompt=_process_model_edit_system_prompt(),
                    user_payload=context,
                    max_output_tokens=max_output_tokens,
                )
                output = ProcessModelEditOutput.model_validate(payload)
                if not output.reply.strip():
                    raise StructuredInterviewProviderError("process model edit reply is empty")
                return output
            except (StructuredInterviewProviderError, ValueError) as exc:
                error = exc
                if attempt == 0:
                    max_output_tokens = _retry_max_output_tokens(max_output_tokens)
        raise StructuredInterviewProviderError(
            "process model edit output validation failed after retry"
        ) from error

    def request_structured_output(
        self,
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        system_prompt: str,
        user_payload: Mapping[str, Any],
        reasoning_effort: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        """Request a validated JSON-shaped response for another agent contract.

        The HTTP/SigV4 and strict-schema handling is shared with the structured
        interview provider. Callers remain responsible for validating the
        returned payload with their own Pydantic model.
        """

        return self._request(
            model=self.model_id,
            reasoning_effort=reasoning_effort,
            schema_name=schema_name,
            schema=schema,
            system_prompt=system_prompt,
            user_payload=user_payload,
            max_output_tokens=max_output_tokens,
        )

    def _request(
        self,
        *,
        model: str,
        reasoning_effort: str,
        schema_name: str,
        schema: Mapping[str, Any],
        system_prompt: str,
        user_payload: Mapping[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        request_body = {
            "model": model,
            "reasoning": {"effort": reasoning_effort},
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(user_payload, ensure_ascii=False),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": _make_strict_schema(schema),
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        request_url = f"{self.endpoint_url}/responses"
        request_body_text = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
        headers = {"content-type": "application/json"}
        try:
            credentials = self.session.get_credentials()
            if credentials is None:
                raise StructuredInterviewProviderError(
                    "AWS credentials are not configured for Bedrock"
                )
            signed_request = AWSRequest(
                method="POST",
                url=request_url,
                data=request_body_text.encode("utf-8"),
                headers=headers,
            )
            SigV4Auth(
                credentials.get_frozen_credentials(),
                "bedrock",
                self.region_name,
            ).add_auth(signed_request)
            with self.http_client_factory(timeout=self.timeout) as client:
                response = client.post(
                    request_url,
                    headers=dict(signed_request.headers),
                    content=request_body_text.encode("utf-8"),
                )
            response.raise_for_status()
            response_json = response.json()
        except StructuredInterviewProviderError:
            raise
        except (BotoCoreError, httpx.HTTPError, ValueError) as exc:
            raise StructuredInterviewProviderError(
                "Amazon Bedrock Structured Outputs request failed"
            ) from exc

        response_status = response_json.get("status")
        incomplete_details = response_json.get("incomplete_details")
        if response_status == "incomplete" or incomplete_details:
            reason = (
                incomplete_details.get("reason")
                if isinstance(incomplete_details, Mapping)
                else None
            )
            logger.warning(
                "structured_interview_provider_incomplete model_id=%s status=%s reason=%s",
                model,
                response_status,
                reason,
            )

        text = _extract_response_text(response_json)
        if not text:
            raise StructuredInterviewProviderError(
                "Amazon Bedrock Structured Outputs response is empty"
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "structured_interview_provider_invalid_json model_id=%s status=%s output_chars=%s",
                model,
                response_status,
                len(text),
            )
            raise StructuredInterviewProviderError(
                "Amazon Bedrock Structured Outputs response is not JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise StructuredInterviewProviderError(
                "Amazon Bedrock Structured Outputs response is not an object"
            )
        return parsed


def _retry_max_output_tokens(current: int) -> int:
    """Give a truncated structured response a larger retry budget."""

    return max(current, min(current * 2, 10_000))


def _extract_response_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = response.get("output")
    if not isinstance(output, Sequence):
        return ""
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, Sequence):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks).strip()


def _make_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic's schema to the strict object requirements."""

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: visit(item)
            for key, item in value.items()
            if key not in {"default", "title"}
        }
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties")
            if isinstance(properties, dict):
                result["required"] = list(properties)
                result["additionalProperties"] = False
        return result

    return visit(dict(schema))


def _interpreter_system_prompt(profile: str, locale: InterviewLocale = "ja-JP") -> str:
    return f"""あなたは構造化インタビューの意味解釈器です。
用途 profile は {profile} です。
最新の発話と会話状態から、発話に明示された情報だけを抽出してください。
返却は指定されたJSON Schemaだけに従い、JSON以外を返さないでください。

必須ルール:
{interview_language_instruction(locale)}
- fieldUpdates、requirementUpdates、processPatch、contradictions、applicability、openIssuesを使用します。
- utteranceCompletenessは、最新発話が文として完結し、現在の質問への意味的な回答として成立している場合だけCOMPLETEにします。文が途中で切れている、接続助詞・動詞の語幹で終わっている、または続きが明らかな場合はINCOMPLETEにします。判断できない場合はUNCERTAINにします。
- Transcribe由来の発話ではtranscriptAssessment.rawTranscriptに最新発話をそのまま入れ、意味を変えない句読点・空白・表記揺れだけをNONEとしてnormalizedTranscriptに反映します。単語や意味を変更する場合はCORRECTEDにし、normalizedTranscriptへ最有力の自然な候補を入れます。候補が一意でない場合はUNCERTAINにし、correctionCandidatesを列挙します。発話にない情報は追加しません。
- correctionStatusがCORRECTEDまたはUNCERTAINの場合、fieldUpdates、requirementUpdates、processPatch、applicabilityを確定可能な情報として返しません。CORRECTEDはBackendが候補を確認質問にし、UNCERTAINは再発話を依頼します。
- answerAssessment.sufficiencyは、SUFFICIENT、PARTIAL、AMBIGUOUS、EXAMPLE_MISSING、REASON_MISSING、CRITERIA_MISSING、UNANSWERABLE、REFUSAL、INCOMPLETEのいずれかです。回答が不足している場合はprobeTypeに不足部分だけを確認する方針（REFRAME、EXAMPLE、REASON、CRITERIA、CLARIFY、RETRY）を入れ、SUFFICIENTならNONEにします。
- currentQuestionまたはfieldsの対象fieldにquestionPlan.requiredItemsがある場合、取得できた各観点をfieldUpdatesに1件ずつ入れ、itemIdにはquestionPlanのitemIdをそのまま設定します。同じfieldIdのfieldUpdatesを複数返して構いません。未取得の観点をfieldUpdatesへ推測で追加してはいけません。
- questionPlan.requiredItemsを使う場合、最新発話で取得できた観点だけを抽出し、既存のinterviewState.fieldStates[fieldId].capturedItemsおよびmissingRequiredItemIdsを参照して、既に取得済みの観点を再度未取得として扱わないでください。
- requiredItemsがすべて取得済みでも、具体例・理由・判断基準などのoptionalItemsを1つ確認した方が回答の価値が明らかに高まる場合だけ、SUFFICIENT以外のanswerAssessmentとprobeTypeを返してください。単なる好みや情報の水増しでは深掘りしません。
- currentQuestion.optionalDeepening=trueの場合は、currentQuestion.deepeningItemsに含まれる観点への回答だけをfieldUpdatesに抽出し、itemIdをそのまま設定してください。追加の深掘り観点や既取得のrequiredItemsを推測で要求してはいけません。
- ユーザーが「どういう意味ですか」「何を答えればよいですか」など、質問の意味や回答方法を尋ねた場合はQUESTION_TO_ASSISTANTまたはCLARIFICATION_REQUESTにし、回答値を作らず、現在の質問対象を維持します。
- 確定判断、質問対象の選択、完了判定はBackendが実行します。あなたは確定済みと返しません。
- 最新の発話から取得できた情報は、複数項目でもすべて候補として抽出します。
- 最新の発話に根拠がない情報を補いません。
- candidateSourceは、最新の発話が事実を述べている場合はuser_statement、利用者が「提案して」「案を出して」などと求め、あなたが例示案を作る場合だけassistant_proposalにします。
- user_statementのfieldUpdatesとrequirementUpdatesにはanswerResolutionを必ず設定します。意味的一致、対象フィールドの型・用途、必要情報量、前後の矛盾、音声の場合は認識信頼度を合わせて、会話を止める必要性を判定してください。
- sttConfidenceが低い、固有名詞・数字・単位・コードが不自然、または複数解釈が残る場合は、推測で確定せずCORRECTEDまたはUNCERTAINとして確認・再発話へ回します。
- answerResolutionはAUTO_CONFIRM（十分に確かな回答。確認せず次の質問へ）、TENTATIVE（回答として成立するが曖昧。候補を保持して次の質問へ）、RETRY（意味的に成立しない、または誤認識の可能性が高い。値を抽出しない）、CONFIRM_REQUIRED（重大な矛盾や例外的な不確実性で停止が必要）のいずれかです。
- 通常の回答を受け取っただけでCONFIRM_REQUIREDにしてはいけません。TENTATIVEでは「はい／いいえ」の確認を生成せず、次の質問生成器が候補を自然に織り込みます。
- 「どういう意味ですか」「何を答えればよいですか」「この質問は何を聞いていますか」のように、質問の意味や回答方法を尋ねる発話はCLARIFICATION_REQUEST（またはQUESTION_TO_ASSISTANT）にし、STTの不確実性とは扱いません。transcriptAssessment.correctionStatusはNONEにし、発話が文として完結していればutteranceCompletenessはCOMPLETEにします。
- 「よく分からないですね」「答えが思いつかない」「覚えていない」「特にありません」のように回答内容が思い浮かばない、または回答できる内容がない発話は、ANSWERとUNANSWERABLEまたはREFUSALで評価します。単に「よく分からない」とだけ言われた場合も、質問の意味を尋ねる表現が含まれていなければ回答不能として扱います。STTが不確実でない限りUNCERTAINにはしません。
- 「うーん」のような考え中の発話はHESITATION、「へえ」のような相づちはBACKCHANNELにし、fieldUpdates、requirementUpdates、processPatch、applicabilityを空にします。
- assistant_proposalの値は利用者の事実として確定していません。候補として返し、確認質問で採用・修正・拒否を促します。
- assistant_proposalを返す場合も、候補を作るきっかけになった最新発話のevidenceTranscriptIdsを設定します。値そのものの根拠が利用者発話にあるとは扱いません。
- 利用者が案を求めた発話では、dialogueActがQUESTION_TO_ASSISTANTでも、提案できる値をassistant_proposalの候補として返してください。提案できない場合は値を推測せず、更新を空にしてください。
- profileがsystem_requirementで、requirement.purpose_problemの提案を求められた場合は、requirement.usersとrequirement.requestがCONFIRMEDのときだけassistant_proposalを返します。どちらかが未確認の場合、目的・課題の候補を作らず、requirementUpdatesを空にします。
- 情報が見つからないことを、存在しないこととして返しません。
- branch、exception、external_system、error_handling、handoff、input_outputは、発話が明示的に存在または不存在を述べた場合だけapplicabilityに入れます。それ以外はunknownのままです。
- applicabilityのnot_applicableには、存在しないことを明示した最新発話のevidenceTranscriptIdsを付けます。
- ProcessModelは意味構造だけを返します。Mermaid、React Flowの座標、画像、表示用コードは返しません。
- processPatch.baseProcessVersionには入力状態のprocessState.versionをそのまま設定します。
- processPatchでは、addNodes/addParticipantsで追加したIDも同一Patch内の参照先として使用できますが、存在しないIDをsourceNodeId、targetNodeId、participantIds、sourceParticipantId、targetParticipantIdに指定してはいけません。確実に構成できない場合は、推測でエッジややり取りを作らず、processPatchを空にしてください。
- processPatchの追加・更新要素はconfirmationStatus=candidate、lifecycle=activeとし、evidenceTranscriptIdsには会話中に存在する根拠メッセージIDだけを入れてください。追加と更新を同じIDで混在させず、更新は入力状態に存在するIDだけを使用してください。
- processPatchRepairが入力に含まれる場合は、前回のprocessPatchがBackend検証で拒否されています。validationErrorsをすべて解消するよう、previousProcessPatchを修正したprocessPatchを返してください。今回の発話と入力状態に根拠がある要素だけを残し、processPatch以外の更新は空にして構いません。
- 矛盾は両立しない情報が会話内にある場合だけ返します。推測で作りません。
- 最新発話で既存の矛盾が解消された場合はresolvedContradictionIdsに対象IDを入れます。
- 対象IDは入力状態のID、または新規要素に対して安定した説明的IDを使用します。
- pending confirmation targetがあり、最新の発話が候補を明確に承認している場合はdialogueActをCONFIRMATIONにします。
- 「はい、大丈夫です。」「はい、そうです。」「問題ありません。」は、候補に対する明確な承認です。
- 「はい、でも…」「ちょっと違うけど、いいよ」「違います」「修正します」のように訂正、留保、追加条件を含む発話はCONFIRMATIONにせず、CORRECTIONまたはREJECTIONにして内容を抽出してください。
""".strip()


def _question_system_prompt(profile: str, locale: InterviewLocale = "ja-JP") -> str:
    return f"""あなたは{profile}用途のインタビュー質問文生成器です。
Backendが選択したtargetについて、質問を1問だけ生成してください。
{interview_language_instruction(locale)}
返却は指定されたJSON Schemaに従ってください。questionTextに加えて、文書から対象項目の値を明示的に読み取れる場合だけdocumentCandidateValueとdocumentCandidateSourceIdsを返してください。根拠がない場合はdocumentCandidateValue=null、documentCandidateSourceIds=[]にしてください。
- questionTextはuser-facingな実際の質問文だけにしてください。回答全文の引用、「なるほど」「そうなんですね」「〜なんですね」の定型リアクション、勝手な長いコメント、target名の説明と同義質問の組み合わせ、「では○○について」＋「○○を教えてください」の二重構造は禁止です。
- 1回の生成で質問は必ず1問だけにしてください。同義の質問を2つ並べたり、targetに含まれない項目を尋ねたりしないでください。targetType=fieldでmissingItemsが指定されている場合は、その一覧にある不足観点だけを1問にまとめて尋ね、capturedItemIdsに含まれる観点や回答済みの内容を聞き直さないでください。
- currentState.answerAssessmentまたはactiveProbeがある場合は、回答済みの内容を繰り返さず、probeTypeが示す不足部分だけを一度に確認してください。activeProbe.missingItemsがあれば、その項目名・説明を使って具体的に質問し、「もう少し詳しく」「他に紹介したいこと」などの抽象的な深掘りに置き換えないでください。UNANSWERABLEやREFUSALへのprobeは中立的な別の聞き方にし、拒否が再度明示されたら質問を続ける前提にしないでください。
- targetType=fieldでoptionalDeepening=trueの場合は、deepeningItemsにある観点を具体的に1問だけ確認してください。回答済みのrequiredItemsやcapturedItemIdsを聞き直さず、対象者が答えたくない・情報がないと示した場合は深掘りを繰り返さない前提で質問してください。
- targetTypeがclosingの場合は、ここまでの質問で扱わなかった重要なことを自由に追加できる、誘導しないopen-endedな質問を1問だけ作ってください。
- tentativeCandidatesは説明文として読み上げず、必要な場合だけ次の質問に自然に関係づけてください。候補を毎回復唱しないでください。
target以外の不足項目を同時に聞かないでください。
retrieved_knowledgeはBackendが検索したindexed済み文書です。documentCandidateValueはtargetの回答として文書本文またはタイトルに明示された、短く具体的な値だけにしてください。推測、一般知識、本文にない要約、別項目の値を候補にしてはいけません。documentCandidateSourceIdsには候補値を直接裏付けるsource_idだけを入れてください。
documentCandidateValueを返した場合は、questionTextでもその候補を文書由来として確認する質問にしてください。Backendが候補値と根拠を検証し、確認待ち状態を作成します。candidateSourceがassistant_proposalの場合は、候補値だけを説明し、確認文の定型化とOKボタン表示はBackend/UIが行います。answerResolutionがTENTATIVEの候補は確認せず、候補を自然に含めて次の質問へつなげてください。
applicability対象には、存在するか、存在しないかを明示的に回答できる質問にしてください。
ProcessModelや図のコードは生成しないでください。
""".strip()


def _process_model_edit_system_prompt() -> str:
    return """あなたはシステム要件、業務フロー、シーケンス図の意味構造を編集するアシスタントです。
管理者の指示を、現在のRequirementStateに対するRequirementPatchおよびProcessModelに対するProcessPatchへ変換してください。
返却は指定されたJSON Schemaだけに従い、JSON以外を返さないでください。

必須ルール:
- 指示はインタビュー回答ではなく、既存の要件またはProcessModelへの管理者編集指示です。
- 既存要件の内容を変更する指示は、requirementPatch.updateRequirementsに入力状態のrequirementIdと変更後の全文を入れてください。新しい要件IDを作らないでください。
- 検索条件、検索結果、表示項目、スコア、並び順、権限、出力形式などのシステム機能の変更は、ProcessModelだけで表現せず、対応する既存要件の値を更新してください。
- 既存の要求内容を変更する場合は、元の要件の意味を保持したうえで、指示内容を統合した変更後の値を返してください。
- 指示が要件と処理モデルの両方に関係する場合は、両方のPatchを返してください。
- 要件を追加・削除する操作は対象外です。既存要件の更新だけを返してください。
- processPatch.baseProcessVersionは入力された現在のバージョンをそのまま設定してください。
- 既存要素を更新する場合は、入力状態にあるIDをそのまま使用してください。
- 指示に明示されていない要素、関係、確認状態、根拠を変更しないでください。
- 追加は指示が明示した場合だけ行い、既存IDと重複しない説明的なIDを使用してください。
- 削除指示は、削除対象のエッジまたはやり取りのIDをremoveEdgesまたはremoveInteractionsに入れてください。
- ノードや参加者を削除する必要がある指示は、削除できない旨をreplyで短く説明し、削除以外の変更を返さないでください。
- sourceNodeId、targetNodeId、sourceParticipantId、targetParticipantIdは入力状態にあるIDだけを使用してください。
- フローチャートのnodeTypeは、開始をstart、処理をactivity、判断をdecision、終了をend、システムをsystem、入出力データをdata、サブプロセスをsubprocessとして返してください。
- 条件分岐、例外、権限エラー、対象データなし、即時処理と非同期処理、バックグラウンド完了通知、引き継ぎを編集する指示では、ProcessStateを正本としてフローチャートとシーケンス図の両方に反映してください。対応するProcessNode/ProcessEdgeだけ、またはProcessParticipant/ProcessInteractionだけを更新して片方を古いままにしてはいけません。
- シーケンス図のProcessInteractionは、通常の送信をmessage、戻り値をreturn、バックグラウンド処理をasync、結果通知をnotification、担当者への引き継ぎをhandoff、異常系をexceptionとしてinteractionTypeに設定してください。条件分岐、任意処理、繰り返しはfragmentTypeのalt、opt、loopで表し、fragmentIdとfragmentLabelで同じ断片と条件を対応づけてください。
- 既存の参加者で表せないシステム、ジョブ、通知先、担当者が指示に明示されている場合だけProcessParticipantを追加し、その参加者を参照するProcessInteractionも追加してください。ユーザーが言っていない参加者や処理結果を推測して追加してはいけません。
- 要件だけを変更する場合、processPatchの各操作配列は空にしてください。
- 変更対象以外の配列は空にしてください。
- replyは要件またはProcessModelに実施した変更を日本語で1〜2文にしてください。変更できない場合は理由と代替案を示してください。
- Mermaidコード、React Flowの座標、画像、表示用コードは返さないでください。
""".strip()
