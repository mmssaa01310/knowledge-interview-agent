from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ai_interviewer_api.agents.learning_support.prompt_loader import (
    load_learning_support_analysis_prompt,
    load_learning_support_personal_advice_prompt,
)
from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProviderError,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.permissions import (
    ensure_record_access,
    ensure_tenant_scope,
    require_dashboard_role,
    require_management_role,
)
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.dashboard import (
    AdminDashboardResponse,
    DashboardFilters,
    DashboardTotals,
    GuidanceAssessment,
    GuidanceDraftResponse,
    GuidanceDraftSummary,
    GuidanceGenerationOutput,
    GuidanceUpdateRequest,
    KnowledgeDashboardSummary,
    LearningAnalysisDraftResponse,
    LearningAnalysisGenerationOutput,
    LearningAnalysisRequest,
    LearningPersonalAdvice,
    LearningPersonalAdviceFocus,
    LearningPersonalAdviceGenerationOutput,
    LearningAnalysisTheme,
    LearningAnalysisUpdateRequest,
    LearningObjectiveTrend,
    LearningStatusSummary,
    RecordReviewPriority,
    ReviewReason,
    TrendPoint,
)
from ai_interviewer_api.services.audit import write_audit_log


_LEARNING_STATUS_MAP = {
    "CONFIRMED": "confirmed",
    "confirmed": "confirmed",
    "CANDIDATE_PENDING": "partially_confirmed",
    "AWAITING_CONFIRMATION": "needs_follow_up",
    "candidate": "partially_confirmed",
}
_LEARNING_STATUS_COUNTER_KEYS = {
    "confirmed": "confirmedCount",
    "partially_confirmed": "partiallyConfirmedCount",
    "not_evidenced": "notEvidencedCount",
    "needs_follow_up": "needsFollowUpCount",
    "not_applicable": "notApplicableCount",
}
_REVIEW_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class GuidanceGenerationError(RuntimeError):
    """Raised when an AI guidance draft cannot be safely validated."""


class LearningAnalysisGenerationError(RuntimeError):
    """Raised when a cross-record learning analysis cannot be safely validated."""


def build_admin_dashboard(
    user: UserContext,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    knowledge_id: str | None = None,
    profile: str | None = None,
    record_status: str | None = None,
    limit: int = 100,
) -> AdminDashboardResponse:
    """Build a management aggregate view without returning conversation content."""

    require_dashboard_role(user)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="dashboard_date_range_invalid")

    timezone_name, display_timezone = _resolve_timezone(getattr(user, "timezone", None))
    filters = DashboardFilters(
        dateFrom=date_from,
        dateTo=date_to,
        knowledgeId=knowledge_id,
        profile=profile,
        recordStatus=record_status,
        timezone=timezone_name,
    )

    knowledges = [
        row
        for row in store.list("knowledges", user.tenant_id)
        if _is_active(row)
        and (knowledge_id is None or row.get("id") == knowledge_id)
        and (profile is None or _knowledge_profile(row) == profile)
    ]
    knowledge_by_id = {str(row["id"]): row for row in knowledges}
    records = [
        row
        for row in store.list("records", user.tenant_id)
        if _is_active(row)
        and str(row.get("knowledgeId")) in knowledge_by_id
        and (record_status is None or row.get("status", "draft") == record_status)
        and _date_in_range(
            _date_key(row.get("createdAt"), display_timezone),
            date_from,
            date_to,
        )
    ]
    record_by_id = {str(row["id"]): row for row in records}
    states_by_record = {
        str(row.get("recordId")): row
        for row in store.list("interview_states", user.tenant_id)
        if str(row.get("recordId")) in record_by_id
    }
    fields_by_knowledge = defaultdict(list)
    for field in store.list("knowledge_fields", user.tenant_id):
        if _is_active(field) and str(field.get("knowledgeId")) in knowledge_by_id:
            fields_by_knowledge[str(field["knowledgeId"])].append(field)

    priority_items = [
        _build_review_priority(
            record,
            knowledge_by_id[str(record["knowledgeId"])],
            states_by_record.get(str(record["id"])),
            fields_by_knowledge.get(str(record["knowledgeId"]), []),
        )
        for record in records
    ]
    review_candidates = [
        item for item in priority_items if item["level"] in {"high", "medium"}
    ]
    review_candidates.sort(
        key=lambda item: (
            _REVIEW_PRIORITY_ORDER[item["level"]],
            item["updatedAt"],
            item["recordId"],
        )
    )

    status_counts = Counter(str(row.get("status", "draft")) for row in records)
    learning_counter = Counter()
    for record in records:
        objectives = build_learning_assessments(
            record,
            states_by_record.get(str(record["id"])),
            fields_by_knowledge.get(str(record["knowledgeId"]), []),
        )
        for objective in objectives:
            learning_counter[objective["status"]] += 1

    guidance_rows = [
        row
        for row in store.list("guidance_drafts", user.tenant_id)
        if _is_active(row) and str(row.get("recordId")) in record_by_id
    ]
    guidance_rows.sort(key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")), reverse=True)

    return AdminDashboardResponse(
        generatedAt=utc_now(),
        filters=filters,
        totals=DashboardTotals(
            knowledgeCount=len(knowledges),
            recordCount=len(records),
            inProgressCount=status_counts.get("in_progress", 0),
            submittedCount=status_counts.get("submitted", 0),
            returnedCount=status_counts.get("returned", 0),
            approvedCount=status_counts.get("approved", 0),
            pendingReviewCount=status_counts.get("submitted", 0),
            highPriorityCount=sum(item["level"] == "high" for item in priority_items),
            mediumPriorityCount=sum(item["level"] == "medium" for item in priority_items),
        ),
        timeSeries=_build_time_series(
            records,
            user.tenant_id,
            record_by_id,
            date_from=date_from,
            date_to=date_to,
            timezone=display_timezone,
        ),
        knowledgeSummaries=[
            _build_knowledge_summary(
                knowledge,
                records,
                priority_items,
            )
            for knowledge in sorted(
                knowledges,
                key=lambda row: (str(row.get("createdAt") or ""), str(row.get("id") or "")),
            )
        ],
        activityByUser=_build_activity_summary(
            records,
            user.tenant_id,
            states_by_record,
            fields_by_knowledge,
        ),
        reviewPriorities=[
            RecordReviewPriority.model_validate(item)
            for item in review_candidates[:limit]
        ],
        reviewPriorityTotal=len(review_candidates),
        learningStatus=LearningStatusSummary(
            confirmed=learning_counter.get("confirmed", 0),
            partiallyConfirmed=learning_counter.get("partially_confirmed", 0),
            notEvidenced=learning_counter.get("not_evidenced", 0),
            needsFollowUp=learning_counter.get("needs_follow_up", 0),
            notApplicable=learning_counter.get("not_applicable", 0),
        ),
        guidanceDrafts=[
            _guidance_summary(row, record_by_id[str(row["recordId"])])
            for row in guidance_rows
        ],
    )


def build_learning_assessments(
    record: Mapping[str, Any],
    interview_state: Mapping[str, Any] | None,
    fields: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive explainable objective states from saved structured interview data."""

    state = interview_state or {}
    field_states = state.get("fieldStates") if isinstance(state.get("fieldStates"), Mapping) else {}
    objectives: list[dict[str, Any]] = []

    for field in sorted(fields, key=lambda row: (row.get("displayOrder", 0), str(row.get("id") or ""))):
        field_id = str(field.get("id") or "")
        if not field_id:
            continue
        field_state = field_states.get(field_id) if isinstance(field_states, Mapping) else None
        evidence_ids = _evidence_ids(field_state)
        status = _assessment_status(field_state)
        objectives.append(
            {
                "objectiveId": f"field:{field_id}",
                "label": str(field.get("name") or field_id),
                "status": status,
                "evidenceIds": evidence_ids,
                "targetType": "field",
                "targetId": field_id,
            }
        )

    requirement_states = state.get("requirementStates")
    if isinstance(requirement_states, Mapping):
        for requirement_id, requirement in sorted(requirement_states.items(), key=lambda item: str(item[0])):
            if not isinstance(requirement, Mapping):
                continue
            objective_id = str(requirement.get("requirementId") or requirement_id)
            objectives.append(
                {
                    "objectiveId": f"requirement:{objective_id}",
                    "label": str(requirement.get("label") or objective_id),
                    "status": _assessment_status(requirement),
                    "evidenceIds": _evidence_ids(requirement),
                    "targetType": "requirement",
                    "targetId": objective_id,
                }
            )

    process_state = state.get("processState")
    process_nodes = process_state.get("nodes") if isinstance(process_state, Mapping) else None
    if isinstance(process_nodes, list):
        for node in process_nodes:
            if not isinstance(node, Mapping) or node.get("lifecycle") == "superseded":
                continue
            node_id = str(node.get("nodeId") or node.get("id") or "")
            if not node_id:
                continue
            objectives.append(
                {
                    "objectiveId": f"process:{node_id}",
                    "label": str(node.get("label") or node_id),
                    "status": (
                        "confirmed"
                        if node.get("confirmationStatus") == "confirmed"
                        else "partially_confirmed"
                    ),
                    "evidenceIds": _string_list(node.get("evidenceTranscriptIds")),
                    "targetType": "process_node",
                    "targetId": node_id,
                }
            )

    return objectives


def generate_guidance_draft(
    record_id: str,
    user: UserContext,
    *,
    provider: Any | None = None,
) -> GuidanceDraftResponse:
    require_management_role(user)
    record = _get_record_for_management(record_id, user)
    knowledge = _get_knowledge(record, user)
    fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if _is_active(row) and str(row.get("knowledgeId")) == str(record["knowledgeId"])
    ]
    state = store.get("interview_states", f"interview-state-{record_id}") or {}
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if str(row.get("recordId")) == record_id and row.get("isActualUtterance", True) is not False
    ]
    objectives = build_learning_assessments(record, state, fields)
    model_id = _resolve_guidance_model_id(knowledge)
    structured_provider = provider or BedrockResponsesStructuredProvider(model_id=model_id)

    context = {
        "record": {
            "id": record_id,
            "title": record.get("title"),
            "status": record.get("status", "draft"),
        },
        "learningObjectives": objectives,
        "interviewState": _guidance_state_snapshot(state),
        "messages": [
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "content": message.get("content"),
            }
            for message in messages
        ],
    }
    system_prompt = """あなたはKIKIORIの教育支援案作成アシスタントです。
対象者を評価・順位付けせず、保存されたインタビュー記録から次の学習・確認行動を提案してください。
入力にない事実や理解不足を推測してはいけません。
各assessmentのevidenceIdsは、入力に含まれるメッセージIDまたは構造化データの根拠IDだけを使用してください。
not_evidencedは「記録に根拠がない」という意味であり、「理解していない」と断定してはいけません。
指導案と学習案内は、指導者が確認する下書きです。JSON Schemaに従うJSONだけを返してください。"""

    try:
        raw_output = structured_provider.request_structured_output(
            schema_name="learning_guidance_output",
            schema=GuidanceGenerationOutput.model_json_schema(),
            system_prompt=system_prompt,
            user_payload=context,
            reasoning_effort=settings.structured_interview_reasoning_effort,
            max_output_tokens=settings.structured_interview_max_output_tokens,
        )
        output = GuidanceGenerationOutput.model_validate(raw_output)
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        raise GuidanceGenerationError("learning guidance output validation failed") from exc

    valid_evidence_ids = _valid_guidance_evidence_ids(messages, objectives)
    objective_by_id = {str(objective["objectiveId"]): objective for objective in objectives}
    generated_by_id: dict[str, Any] = {}
    for assessment in output.assessments:
        objective = objective_by_id.get(assessment.objectiveId)
        if objective is None or assessment.objectiveId in generated_by_id:
            raise GuidanceGenerationError("learning guidance objective reference is invalid")
        if not set(assessment.evidenceIds).issubset(valid_evidence_ids):
            raise GuidanceGenerationError("learning guidance evidence reference is invalid")
        generated_by_id[assessment.objectiveId] = assessment

    assessment_rows = []
    for objective in objectives:
        generated = generated_by_id.get(objective["objectiveId"])
        assessment_rows.append(
            GuidanceAssessment(
                objectiveId=objective["objectiveId"],
                label=objective["label"],
                status=objective["status"],
                suggestedStatus=generated.status if generated else None,
                evidenceIds=objective["evidenceIds"],
                learnerGuidance=(generated.learnerGuidance.strip() if generated else ""),
                instructorGuidance=(generated.instructorGuidance.strip() if generated else ""),
                followUpQuestion=(generated.followUpQuestion.strip() if generated else ""),
            )
        )

    summary = output.summary.strip()
    learner_guidance = output.learnerGuidance.strip()
    instructor_guidance = output.instructorGuidance.strip()
    if not summary or not learner_guidance or not instructor_guidance:
        raise GuidanceGenerationError("learning guidance text is empty")

    now = utc_now()
    draft = {
        "id": str(uuid4()),
        "tenantId": user.tenant_id,
        "createdByUserId": user.user_id,
        "updatedByUserId": user.user_id,
        "ownerUserId": record.get("ownerUserId"),
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
        "recordId": record_id,
        "knowledgeId": record["knowledgeId"],
        "status": "draft",
        "modelId": model_id,
        "inputVersion": int(state.get("stateVersion", 0) or 0),
        "summary": summary,
        "learnerGuidance": learner_guidance,
        "instructorGuidance": instructor_guidance,
        "assessments": [row.model_dump() for row in assessment_rows],
        "generatedAt": now,
        "publishedAt": None,
        "publishedByUserId": None,
    }
    store.upsert("guidance_drafts", draft)
    write_audit_log(
        user,
        "guidance_generate",
        "guidance_draft",
        draft["id"],
        {
            "recordId": record_id,
            "knowledgeId": record["knowledgeId"],
            "modelId": model_id,
            "inputVersion": draft["inputVersion"],
        },
    )
    return _guidance_response(draft)


def generate_learning_analysis(
    user: UserContext,
    request: LearningAnalysisRequest,
    *,
    provider: Any | None = None,
) -> LearningAnalysisDraftResponse:
    """Generate a reviewed-by-people learning support draft across one knowledge."""

    require_dashboard_role(user)
    if request.dateFrom and request.dateTo and request.dateFrom > request.dateTo:
        raise HTTPException(status_code=422, detail="dashboard_date_range_invalid")

    knowledge = store.get("knowledges", request.knowledgeId)
    if not knowledge or not _is_active(knowledge):
        raise HTTPException(status_code=404, detail="knowledge_not_found")
    ensure_tenant_scope(user, str(knowledge.get("tenantId") or ""))
    if request.profile and _knowledge_profile(knowledge) != request.profile:
        raise HTTPException(status_code=422, detail="learning_analysis_profile_mismatch")

    timezone_name, display_timezone = _resolve_timezone(getattr(user, "timezone", None))
    records = [
        row
        for row in store.list("records", user.tenant_id)
        if _is_active(row)
        and str(row.get("knowledgeId")) == request.knowledgeId
        and (request.recordStatus is None or row.get("status", "draft") == request.recordStatus)
        and _date_in_range(
            _date_key(row.get("createdAt"), display_timezone),
            request.dateFrom,
            request.dateTo,
        )
    ]
    if len(records) < 2:
        raise HTTPException(status_code=422, detail="learning_analysis_requires_multiple_records")

    fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if _is_active(row) and str(row.get("knowledgeId")) == request.knowledgeId
    ]
    record_ids = [str(row["id"]) for row in records]
    record_id_set = set(record_ids)
    states_by_record = {
        str(row.get("recordId")): row
        for row in store.list("interview_states", user.tenant_id)
        if str(row.get("recordId")) in record_id_set
    }
    objective_trends = _build_learning_objective_trends(records, states_by_record, fields)
    messages_by_record: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for message in store.list("messages", user.tenant_id):
        record_id = str(message.get("recordId") or "")
        if (
            record_id in record_id_set
            and message.get("role") == "user"
            and message.get("turnType") != "CONTROL"
            and message.get("isActualUtterance", True) is not False
        ):
            messages_by_record[record_id].append(message)

    record_contexts = [
        {
            "id": str(record["id"]),
            "title": record.get("title"),
            "status": record.get("status", "draft"),
            "learningObjectives": build_learning_assessments(
                record,
                states_by_record.get(str(record["id"])),
                fields,
            ),
            "answerExcerpts": _analysis_answer_excerpts(
                messages_by_record.get(str(record["id"]), [])
            ),
            "stateSignals": _analysis_state_signals(
                states_by_record.get(str(record["id"]), {})
            ),
        }
        for record in records
    ]
    context = {
        "scope": {
            "knowledgeId": request.knowledgeId,
            "knowledgeName": knowledge.get("name"),
            "dateFrom": request.dateFrom.isoformat() if request.dateFrom else None,
            "dateTo": request.dateTo.isoformat() if request.dateTo else None,
            "profile": request.profile,
            "recordStatus": request.recordStatus,
            "timezone": timezone_name,
            "recordCount": len(records),
        },
        "objectiveTrends": [trend.model_dump() for trend in objective_trends],
        "records": record_contexts,
    }
    respondent_contexts, respondent_metadata = _build_learning_respondent_contexts(
        records,
        record_contexts,
    )
    model_id = _resolve_guidance_model_id(knowledge)
    structured_provider = provider or BedrockResponsesStructuredProvider(model_id=model_id)

    try:
        raw_output = structured_provider.request_structured_output(
            schema_name="learning_support_analysis_output",
            schema=LearningAnalysisGenerationOutput.model_json_schema(),
            system_prompt=load_learning_support_analysis_prompt(),
            user_payload=context,
            reasoning_effort=settings.structured_interview_medium_reasoning_effort,
            max_output_tokens=settings.structured_interview_max_output_tokens,
        )
        output = LearningAnalysisGenerationOutput.model_validate(raw_output)
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        raise LearningAnalysisGenerationError(
            "learning support analysis output validation failed"
        ) from exc

    summary = output.summary.strip()
    trend_summary = output.trendSummary.strip()
    learner_guidance = output.learnerGuidance.strip()
    instructor_guidance = output.instructorGuidance.strip()
    if not summary or not trend_summary or not learner_guidance or not instructor_guidance:
        raise LearningAnalysisGenerationError("learning support analysis text is empty")

    valid_objective_ids = {trend.objectiveId for trend in objective_trends}
    theme_rows: list[LearningAnalysisTheme] = []
    theme_ids: set[str] = set()
    for theme in output.themes:
        if theme.themeId in theme_ids:
            raise LearningAnalysisGenerationError("learning support analysis theme is duplicated")
        if not set(theme.objectiveIds).issubset(valid_objective_ids):
            raise LearningAnalysisGenerationError(
                "learning support analysis objective reference is invalid"
            )
        if not set(theme.evidenceRecordIds).issubset(record_id_set):
            raise LearningAnalysisGenerationError(
                "learning support analysis record reference is invalid"
            )
        if not theme.objectiveIds or not theme.evidenceRecordIds:
            raise LearningAnalysisGenerationError(
                "learning support analysis theme must have evidence"
            )
        theme_ids.add(theme.themeId)
        theme_rows.append(
            LearningAnalysisTheme(
                themeId=theme.themeId,
                title=theme.title.strip(),
                summary=theme.summary.strip(),
                objectiveIds=theme.objectiveIds,
                evidenceRecordIds=theme.evidenceRecordIds,
                learnerGuidance=theme.learnerGuidance.strip(),
                instructorGuidance=theme.instructorGuidance.strip(),
                followUpQuestion=theme.followUpQuestion.strip(),
            )
        )

    personal_advice_rows = _generate_learning_personal_advice(
        structured_provider,
        context=context,
        respondent_contexts=respondent_contexts,
        respondent_metadata=respondent_metadata,
        overall_summary=summary,
        overall_trend_summary=trend_summary,
        overall_themes=theme_rows,
        valid_objective_ids=valid_objective_ids,
    )

    scope = {
        "dateFrom": request.dateFrom.isoformat() if request.dateFrom else None,
        "dateTo": request.dateTo.isoformat() if request.dateTo else None,
        "knowledgeId": request.knowledgeId,
        "profile": request.profile,
        "recordStatus": request.recordStatus,
        "timezone": timezone_name,
        "recordCount": len(records),
        "recordIds": record_ids,
    }
    now = utc_now()
    draft = {
        "id": str(uuid4()),
        "tenantId": user.tenant_id,
        "createdByUserId": user.user_id,
        "updatedByUserId": user.user_id,
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
        "knowledgeId": request.knowledgeId,
        "knowledgeName": str(knowledge.get("name") or ""),
        "status": "draft",
        "modelId": model_id,
        "scope": scope,
        "objectiveTrends": [trend.model_dump() for trend in objective_trends],
        "summary": summary,
        "trendSummary": trend_summary,
        "learnerGuidance": learner_guidance,
        "instructorGuidance": instructor_guidance,
        "themes": [theme.model_dump() for theme in theme_rows],
        "personalAdvice": [advice.model_dump() for advice in personal_advice_rows],
        "generatedAt": now,
        "reviewedAt": None,
        "reviewedByUserId": None,
    }
    store.upsert("learning_analysis_drafts", draft)
    write_audit_log(
        user,
        "learning_analysis_generate",
        "learning_analysis_draft",
        draft["id"],
        {
            "knowledgeId": request.knowledgeId,
            "recordCount": len(records),
            "analysisStages": ["overall", "respondent_personal_advice"],
            "respondentCount": len(personal_advice_rows),
            "modelId": model_id,
        },
    )
    return _learning_analysis_response(draft)


def list_learning_analyses(
    user: UserContext,
    *,
    knowledge_id: str | None = None,
    limit: int = 20,
) -> list[LearningAnalysisDraftResponse]:
    require_dashboard_role(user)
    if knowledge_id:
        knowledge = store.get("knowledges", knowledge_id)
        if not knowledge or not _is_active(knowledge):
            raise HTTPException(status_code=404, detail="knowledge_not_found")
        ensure_tenant_scope(user, str(knowledge.get("tenantId") or ""))
    rows = [
        row
        for row in store.list("learning_analysis_drafts", user.tenant_id)
        if _is_active(row)
        and (knowledge_id is None or str(row.get("knowledgeId")) == knowledge_id)
    ]
    rows.sort(
        key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    return [_learning_analysis_response(row) for row in rows[:limit]]


def update_learning_analysis(
    analysis_id: str,
    payload: LearningAnalysisUpdateRequest,
    user: UserContext,
) -> LearningAnalysisDraftResponse:
    require_dashboard_role(user)
    draft = _get_learning_analysis_for_management(analysis_id, user)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="learning_analysis_update_empty")
    for key, value in updates.items():
        if isinstance(value, str):
            if not value.strip():
                raise HTTPException(status_code=422, detail="learning_analysis_text_required")
            draft[key] = value.strip()
    draft.update(
        {
            "status": "draft",
            "reviewedAt": None,
            "reviewedByUserId": None,
            "updatedByUserId": user.user_id,
            "updatedAt": utc_now(),
        }
    )
    store.upsert("learning_analysis_drafts", draft)
    write_audit_log(
        user,
        "learning_analysis_update",
        "learning_analysis_draft",
        analysis_id,
        {"fields": sorted(updates)},
    )
    return _learning_analysis_response(draft)


def review_learning_analysis(
    analysis_id: str,
    user: UserContext,
) -> LearningAnalysisDraftResponse:
    require_dashboard_role(user)
    draft = _get_learning_analysis_for_management(analysis_id, user)
    for key in ("summary", "trendSummary", "learnerGuidance", "instructorGuidance"):
        if not str(draft.get(key) or "").strip():
            raise HTTPException(status_code=422, detail="learning_analysis_text_required")
    now = utc_now()
    draft.update(
        {
            "status": "reviewed",
            "reviewedAt": now,
            "reviewedByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "updatedAt": now,
        }
    )
    store.upsert("learning_analysis_drafts", draft)
    write_audit_log(
        user,
        "learning_analysis_review",
        "learning_analysis_draft",
        analysis_id,
        {"knowledgeId": draft.get("knowledgeId")},
    )
    return _learning_analysis_response(draft)


def list_guidance_for_record(record_id: str, user: UserContext, *, public: bool = False) -> list[GuidanceDraftResponse]:
    record = _get_record_for_access(record_id, user)
    if public:
        if user.role != "interviewer":
            raise HTTPException(status_code=403, detail="guidance_public_access_forbidden")
        rows = [
            row
            for row in store.list("guidance_drafts", user.tenant_id)
            if _is_active(row)
            and str(row.get("recordId")) == record_id
            and row.get("status") == "published"
        ]
        return [_guidance_response(row, public=True) for row in _sort_guidance(rows)]

    require_management_role(user)
    return [
        _guidance_response(row)
        for row in _sort_guidance(
            [
                row
                for row in store.list("guidance_drafts", user.tenant_id)
                if _is_active(row) and str(row.get("recordId")) == record_id
            ]
        )
    ]


def update_guidance_draft(
    draft_id: str,
    payload: GuidanceUpdateRequest,
    user: UserContext,
) -> GuidanceDraftResponse:
    require_management_role(user)
    draft = _get_guidance_for_management(draft_id, user)
    if draft.get("status") == "published":
        raise HTTPException(status_code=409, detail="published_guidance_must_be_unpublished")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if isinstance(value, str):
            draft[key] = value.strip()
    draft["updatedByUserId"] = user.user_id
    draft["updatedAt"] = utc_now()
    store.upsert("guidance_drafts", draft)
    write_audit_log(user, "guidance_update", "guidance_draft", draft_id, {"fields": sorted(updates)})
    return _guidance_response(draft)


def publish_guidance_draft(draft_id: str, user: UserContext) -> GuidanceDraftResponse:
    require_management_role(user)
    draft = _get_guidance_for_management(draft_id, user)
    if draft.get("status") == "published":
        return _guidance_response(draft)
    if not str(draft.get("summary") or "").strip():
        raise HTTPException(status_code=422, detail="guidance_summary_required")
    if not str(draft.get("learnerGuidance") or "").strip():
        raise HTTPException(status_code=422, detail="guidance_learner_text_required")
    now = utc_now()
    draft.update(
        {
            "status": "published",
            "publishedAt": now,
            "publishedByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "updatedAt": now,
        }
    )
    store.upsert("guidance_drafts", draft)
    write_audit_log(user, "guidance_publish", "guidance_draft", draft_id, {"recordId": draft["recordId"]})
    return _guidance_response(draft)


def unpublish_guidance_draft(draft_id: str, user: UserContext) -> GuidanceDraftResponse:
    require_management_role(user)
    draft = _get_guidance_for_management(draft_id, user)
    if draft.get("status") != "published":
        return _guidance_response(draft)
    draft.update(
        {
            "status": "unpublished",
            "updatedByUserId": user.user_id,
            "updatedAt": utc_now(),
        }
    )
    store.upsert("guidance_drafts", draft)
    write_audit_log(user, "guidance_unpublish", "guidance_draft", draft_id, {"recordId": draft["recordId"]})
    return _guidance_response(draft)


def _build_review_priority(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    state: Mapping[str, Any] | None,
    fields: list[Mapping[str, Any]],
) -> dict[str, Any]:
    current_state = state or {}
    reasons: list[dict[str, Any]] = []
    score = 0
    status = str(record.get("status", "draft"))
    if status == "returned":
        score += 5
        reasons.append(
            _reason(
                "returned_record",
                "record",
                str(record.get("id")),
                None,
            )
        )

    contradictions = current_state.get("contradictions")
    if isinstance(contradictions, list):
        for contradiction in contradictions:
            if not isinstance(contradiction, Mapping):
                continue
            score += 5
            reasons.append(
                _reason(
                    "contradiction_detected",
                    "contradiction",
                    str(contradiction.get("contradictionId") or ""),
                    str(contradiction.get("topic") or "") or None,
                    _string_list(contradiction.get("evidenceTranscriptIds")),
                )
            )

    open_issues = current_state.get("openIssues")
    if isinstance(open_issues, list):
        for issue in open_issues:
            if not isinstance(issue, Mapping):
                continue
            score += 3
            reasons.append(
                _reason(
                    "open_issue",
                    "issue",
                    str(issue.get("issueId") or ""),
                    str(issue.get("topic") or "") or None,
                    _string_list(issue.get("evidenceTranscriptIds")),
                )
            )

    field_states = current_state.get("fieldStates")
    if not isinstance(field_states, Mapping):
        field_states = {}
    for field in fields:
        if not field.get("required"):
            continue
        field_id = str(field.get("id") or "")
        field_state = field_states.get(field_id)
        if _assessment_status(field_state) != "confirmed":
            score += 2
            reasons.append(
                _reason(
                    "required_item_unconfirmed",
                    "field",
                    field_id,
                    str(field.get("name") or field_id),
                    _evidence_ids(field_state),
                )
            )

    requirement_states = current_state.get("requirementStates")
    if isinstance(requirement_states, Mapping):
        for requirement_id, requirement in requirement_states.items():
            if not isinstance(requirement, Mapping) or _assessment_status(requirement) == "confirmed":
                continue
            score += 2
            target_id = str(requirement.get("requirementId") or requirement_id)
            reasons.append(
                _reason(
                    "requirement_unconfirmed",
                    "requirement",
                    target_id,
                    str(requirement.get("label") or target_id),
                    _evidence_ids(requirement),
                )
            )

    applicability_state = current_state.get("applicabilityState")
    if isinstance(applicability_state, Mapping):
        for topic, applicability in applicability_state.items():
            if not isinstance(applicability, Mapping) or applicability.get("status") != "unknown":
                continue
            score += 1
            reasons.append(
                _reason(
                    "applicability_unconfirmed",
                    "applicability",
                    str(topic),
                    str(topic),
                    _string_list(applicability.get("evidenceTranscriptIds")),
                )
            )

    level = "high" if score >= 5 else "medium" if score > 0 else "low"
    owner_user_id = str(record.get("ownerUserId")) if record.get("ownerUserId") else None
    return {
        "recordId": str(record["id"]),
        "knowledgeId": str(record["knowledgeId"]),
        "knowledgeDbId": str(knowledge.get("knowledgeDbId") or ""),
        "knowledgeName": str(knowledge.get("name") or record.get("knowledgeName") or ""),
        "title": str(record.get("title") or ""),
        "ownerUserId": owner_user_id,
        "ownerDisplayName": _display_name(owner_user_id),
        "recordStatus": status,
        "level": level,
        "reasons": reasons,
        "updatedAt": str(record.get("updatedAt") or record.get("createdAt") or ""),
    }


def _build_knowledge_summary(
    knowledge: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    priority_items: list[Mapping[str, Any]],
) -> KnowledgeDashboardSummary:
    knowledge_id = str(knowledge["id"])
    knowledge_records = [row for row in records if str(row.get("knowledgeId")) == knowledge_id]
    counts = Counter(str(row.get("status", "draft")) for row in knowledge_records)
    priorities = [item for item in priority_items if str(item.get("knowledgeId")) == knowledge_id]
    return KnowledgeDashboardSummary(
        knowledgeId=knowledge_id,
        knowledgeDbId=str(knowledge.get("knowledgeDbId") or ""),
        knowledgeName=str(knowledge.get("name") or ""),
        profile=_knowledge_profile(knowledge),
        recordCount=len(knowledge_records),
        inProgressCount=counts.get("in_progress", 0),
        submittedCount=counts.get("submitted", 0),
        returnedCount=counts.get("returned", 0),
        approvedCount=counts.get("approved", 0),
        highPriorityCount=sum(item.get("level") == "high" for item in priorities),
        mediumPriorityCount=sum(item.get("level") == "medium" for item in priorities),
    )


def _build_activity_summary(
    records: list[Mapping[str, Any]],
    tenant_id: str,
    states_by_record: Mapping[str, Mapping[str, Any]],
    fields_by_knowledge: Mapping[str, list[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    activity: dict[str, dict[str, Any]] = {}
    records_by_owner: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    record_by_id = {str(record.get("id")): record for record in records}
    for record in records:
        owner_id = record.get("ownerUserId")
        if owner_id:
            records_by_owner[str(owner_id)].append(record)

    for message in store.list("messages", tenant_id):
        if (
            message.get("role") != "user"
            or message.get("turnType") == "CONTROL"
            or message.get("isActualUtterance", True) is False
        ):
            continue
        record_id = str(message.get("recordId") or "")
        record = record_by_id.get(record_id)
        if record is None or not record.get("ownerUserId"):
            continue
        owner_id = str(record["ownerUserId"])
        row = activity.setdefault(owner_id, _new_activity_summary(owner_id))
        row["answerCount"] += 1
        created_at = message.get("createdAt")
        if created_at and (
            row["lastActivityAt"] is None or str(created_at) > str(row["lastActivityAt"])
        ):
            row["lastActivityAt"] = created_at

    for owner_id, owner_records in records_by_owner.items():
        row = activity.setdefault(owner_id, _new_activity_summary(owner_id))
        row["recordCount"] = len(owner_records)
        row["submittedCount"] = sum(
            str(record.get("status")) in {"submitted", "returned", "approved"}
            for record in owner_records
        )
        for record in owner_records:
            objectives = build_learning_assessments(
                record,
                states_by_record.get(str(record.get("id"))),
                fields_by_knowledge.get(str(record.get("knowledgeId")), []),
            )
            for objective in objectives:
                counter_key = _LEARNING_STATUS_COUNTER_KEYS.get(
                    str(objective.get("status")),
                    "notEvidenced",
                )
                row[counter_key] += 1
            updated_at = record.get("updatedAt")
            if updated_at and (
                row["lastActivityAt"] is None or str(updated_at) > str(row["lastActivityAt"])
            ):
                row["lastActivityAt"] = updated_at

    return sorted(
        activity.values(),
        # Activity is an operational count, not a ranking of interviewees.
        key=lambda row: (str(row["displayName"]), str(row["userId"])),
    )


def _new_activity_summary(owner_id: str) -> dict[str, Any]:
    return {
        "userId": owner_id,
        "displayName": _display_name(owner_id),
        "recordCount": 0,
        "answerCount": 0,
        "submittedCount": 0,
        "confirmedCount": 0,
        "partiallyConfirmedCount": 0,
        "notEvidencedCount": 0,
        "needsFollowUpCount": 0,
        "notApplicableCount": 0,
        "lastActivityAt": None,
    }


def _build_learning_objective_trends(
    records: list[Mapping[str, Any]],
    states_by_record: Mapping[str, Mapping[str, Any]],
    fields: list[Mapping[str, Any]],
) -> list[LearningObjectiveTrend]:
    trends: dict[str, dict[str, Any]] = {}
    for record in records:
        objectives = build_learning_assessments(
            record,
            states_by_record.get(str(record.get("id"))),
            fields,
        )
        for objective in objectives:
            objective_id = str(objective["objectiveId"])
            trend = trends.setdefault(
                objective_id,
                {
                    "objectiveId": objective_id,
                    "label": str(objective.get("label") or objective_id),
                    "recordCount": 0,
                    "confirmedCount": 0,
                    "partiallyConfirmedCount": 0,
                    "notEvidencedCount": 0,
                    "needsFollowUpCount": 0,
                    "notApplicableCount": 0,
                },
            )
            trend["recordCount"] += 1
            counter_key = _LEARNING_STATUS_COUNTER_KEYS.get(
                str(objective.get("status")),
                "notEvidenced",
            )
            trend[counter_key] += 1
    return [LearningObjectiveTrend.model_validate(value) for value in trends.values()]


def _analysis_answer_excerpts(messages: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    selected = messages[:4]
    if len(messages) > 4:
        selected.extend(messages[-4:])
    seen: set[str] = set()
    excerpts: list[dict[str, str]] = []
    for message in selected:
        message_id = str(message.get("id") or "")
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        content = str(message.get("content") or "").strip()
        if content:
            excerpts.append(
                {
                    "id": message_id,
                    "content": content[:800],
                }
            )
    return excerpts


def _analysis_state_signals(state: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    signals: dict[str, list[dict[str, Any]]] = {
        "contradictions": [],
        "openIssues": [],
    }
    for key in signals:
        values = state.get(key)
        if not isinstance(values, list):
            continue
        signals[key] = [
            {
                "id": str(item.get("contradictionId") or item.get("issueId") or ""),
                "topic": str(item.get("topic") or ""),
                "evidenceIds": _string_list(item.get("evidenceTranscriptIds")),
            }
            for item in values
            if isinstance(item, Mapping)
        ]
    return signals


def _build_learning_respondent_contexts(
    records: list[Mapping[str, Any]],
    record_contexts: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Group analysis input by respondent without sending user IDs to the LLM."""

    record_context_by_id = {
        str(record_context.get("id")): record_context
        for record_context in record_contexts
    }
    contexts: list[dict[str, Any]] = []
    context_by_key: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    key_by_owner: dict[str, str] = {}
    for record in records:
        owner_id = str(record.get("ownerUserId") or "").strip()
        record_id = str(record.get("id") or "").strip()
        if not owner_id or not record_id:
            continue
        respondent_key = key_by_owner.get(owner_id)
        if respondent_key is None:
            respondent_key = f"respondent-{len(key_by_owner) + 1}"
            key_by_owner[owner_id] = respondent_key
            metadata[respondent_key] = {
                "respondentId": owner_id,
                "displayName": _display_name(owner_id) or owner_id,
                "recordIds": [],
            }
            respondent_context = {"respondentKey": respondent_key, "records": []}
            contexts.append(respondent_context)
            context_by_key[respondent_key] = respondent_context
        metadata[respondent_key]["recordIds"].append(record_id)
        record_context = record_context_by_id.get(record_id)
        if record_context is not None:
            context_by_key[respondent_key]["records"].append(dict(record_context))
    return contexts, metadata


def _generate_learning_personal_advice(
    structured_provider: Any,
    *,
    context: Mapping[str, Any],
    respondent_contexts: list[Mapping[str, Any]],
    respondent_metadata: Mapping[str, Mapping[str, Any]],
    overall_summary: str,
    overall_trend_summary: str,
    overall_themes: list[LearningAnalysisTheme],
    valid_objective_ids: set[str],
) -> list[LearningPersonalAdvice]:
    """Run and validate the respondent-specific second analysis stage."""

    if not respondent_metadata:
        return []

    try:
        raw_output = structured_provider.request_structured_output(
            schema_name="learning_support_personal_advice_output",
            schema=LearningPersonalAdviceGenerationOutput.model_json_schema(),
            system_prompt=load_learning_support_personal_advice_prompt(),
            user_payload={
                "scope": context.get("scope", {}),
                "objectiveTrends": context.get("objectiveTrends", []),
                "overallAnalysis": {
                    "summary": overall_summary,
                    "trendSummary": overall_trend_summary,
                    "themes": [theme.model_dump() for theme in overall_themes],
                },
                "respondents": respondent_contexts,
            },
            reasoning_effort=settings.structured_interview_medium_reasoning_effort,
            max_output_tokens=settings.structured_interview_max_output_tokens,
        )
        output = LearningPersonalAdviceGenerationOutput.model_validate(raw_output)
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        raise LearningAnalysisGenerationError(
            "learning support personal advice output validation failed"
        ) from exc

    expected_keys = set(respondent_metadata)
    advice_by_key: dict[str, Any] = {}
    for advice in output.advice:
        respondent_key = advice.respondentKey.strip()
        if respondent_key in advice_by_key:
            raise LearningAnalysisGenerationError(
                "learning support personal advice respondent is duplicated"
            )
        if respondent_key not in expected_keys:
            raise LearningAnalysisGenerationError(
                "learning support personal advice respondent reference is invalid"
            )
        advice_by_key[respondent_key] = advice
    if set(advice_by_key) != expected_keys:
        raise LearningAnalysisGenerationError(
            "learning support personal advice respondent coverage is incomplete"
        )

    rows: list[LearningPersonalAdvice] = []
    for respondent_key, metadata in respondent_metadata.items():
        advice = advice_by_key[respondent_key]
        summary = advice.summary.strip()
        if not summary:
            raise LearningAnalysisGenerationError(
                "learning support personal advice summary is empty"
            )
        valid_record_id_list = [
            str(record_id)
            for record_id in metadata.get("recordIds", [])
            if str(record_id).strip()
        ]
        valid_record_ids = set(valid_record_id_list)
        focus_rows: list[LearningPersonalAdviceFocus] = []
        for focus in advice.focusAreas:
            objective_ids = [str(value).strip() for value in focus.objectiveIds if str(value).strip()]
            evidence_record_ids = [
                str(value).strip()
                for value in focus.evidenceRecordIds
                if str(value).strip()
            ]
            if not objective_ids or not evidence_record_ids:
                raise LearningAnalysisGenerationError(
                    "learning support personal advice focus must have evidence"
                )
            if not set(objective_ids).issubset(valid_objective_ids):
                raise LearningAnalysisGenerationError(
                    "learning support personal advice objective reference is invalid"
                )
            if not set(evidence_record_ids).issubset(valid_record_ids):
                raise LearningAnalysisGenerationError(
                    "learning support personal advice record reference is invalid"
                )
            title = focus.title.strip()
            focus_summary = focus.summary.strip()
            next_step = focus.nextStep.strip()
            follow_up_question = focus.followUpQuestion.strip()
            if not title or not focus_summary or not next_step or not follow_up_question:
                raise LearningAnalysisGenerationError(
                    "learning support personal advice focus text is empty"
                )
            focus_rows.append(
                LearningPersonalAdviceFocus(
                    title=title,
                    summary=focus_summary,
                    objectiveIds=objective_ids,
                    evidenceRecordIds=evidence_record_ids,
                    nextStep=next_step,
                    followUpQuestion=follow_up_question,
                )
            )

        next_steps = [str(value).strip() for value in advice.nextSteps if str(value).strip()]
        follow_up_questions = [
            str(value).strip()
            for value in advice.followUpQuestions
            if str(value).strip()
        ]
        rows.append(
            LearningPersonalAdvice(
                respondentId=str(metadata["respondentId"]),
                displayName=str(metadata["displayName"]),
                recordIds=valid_record_id_list,
                summary=summary,
                focusAreas=focus_rows,
                nextSteps=next_steps,
                followUpQuestions=follow_up_questions,
            )
        )
    return rows


def _build_time_series(
    records: list[Mapping[str, Any]],
    tenant_id: str,
    record_by_id: Mapping[str, Mapping[str, Any]],
    *,
    date_from: date | None,
    date_to: date | None,
    timezone: ZoneInfo,
) -> list[TrendPoint]:
    points: dict[date, dict[str, int]] = defaultdict(
        lambda: {"createdCount": 0, "submittedCount": 0, "approvedCount": 0}
    )
    for record in records:
        record_date = _date_key(record.get("createdAt"), timezone)
        if record_date is not None:
            points[record_date]["createdCount"] += 1

    for audit in store.list("audit_logs", tenant_id):
        record_id = str(audit.get("resourceId") or "")
        if audit.get("resourceType") != "record" or record_id not in record_by_id:
            continue
        event_date = _date_key(audit.get("createdAt"), timezone)
        if event_date is None or not _date_in_range(event_date, date_from, date_to):
            continue
        detail = audit.get("detail")
        target_status = detail.get("to") if isinstance(detail, Mapping) else None
        if target_status == "submitted":
            points[event_date]["submittedCount"] += 1
        elif target_status == "approved":
            points[event_date]["approvedCount"] += 1

    return [
        TrendPoint(date=point_date, **points[point_date])
        for point_date in sorted(points)
        if _date_in_range(point_date, date_from, date_to)
    ]


def _guidance_summary(row: Mapping[str, Any], record: Mapping[str, Any]) -> GuidanceDraftSummary:
    return GuidanceDraftSummary(
        id=str(row["id"]),
        recordId=str(row["recordId"]),
        knowledgeId=str(row["knowledgeId"]),
        recordTitle=str(record.get("title") or ""),
        status=row.get("status", "draft"),
        modelId=str(row.get("modelId") or ""),
        updatedAt=str(row.get("updatedAt") or row.get("createdAt") or ""),
    )


def _learning_analysis_response(row: Mapping[str, Any]) -> LearningAnalysisDraftResponse:
    response_keys = {
        "id",
        "knowledgeId",
        "knowledgeName",
        "status",
        "modelId",
        "scope",
        "objectiveTrends",
        "summary",
        "trendSummary",
        "learnerGuidance",
        "instructorGuidance",
        "themes",
        "personalAdvice",
        "generatedAt",
        "reviewedAt",
        "reviewedByUserId",
        "updatedAt",
    }
    response = {key: row.get(key) for key in response_keys}
    response["objectiveTrends"] = [
        dict(trend)
        for trend in row.get("objectiveTrends", [])
        if isinstance(trend, Mapping)
    ]
    response["themes"] = [
        dict(theme)
        for theme in row.get("themes", [])
        if isinstance(theme, Mapping)
    ]
    response["personalAdvice"] = [
        dict(advice)
        for advice in row.get("personalAdvice", [])
        if isinstance(advice, Mapping)
    ]
    return LearningAnalysisDraftResponse.model_validate(response)


def _get_record_for_management(record_id: str, user: UserContext) -> dict[str, Any]:
    record = store.get("records", record_id)
    if not record:
        raise HTTPException(status_code=404, detail="record_not_found")
    ensure_record_access(record, user, operation="read")
    return record


def _get_record_for_access(record_id: str, user: UserContext) -> dict[str, Any]:
    record = store.get("records", record_id)
    if not record:
        raise HTTPException(status_code=404, detail="record_not_found")
    ensure_record_access(record, user, operation="read")
    return record


def _get_knowledge(record: Mapping[str, Any], user: UserContext) -> dict[str, Any]:
    knowledge = store.get("knowledges", str(record["knowledgeId"]))
    if not knowledge:
        raise HTTPException(status_code=404, detail="knowledge_not_found")
    ensure_tenant_scope(user, knowledge["tenantId"])
    return knowledge


def _get_guidance_for_management(draft_id: str, user: UserContext) -> dict[str, Any]:
    draft = store.get("guidance_drafts", draft_id)
    if not draft or not _is_active(draft):
        raise HTTPException(status_code=404, detail="guidance_not_found")
    record = _get_record_for_management(str(draft["recordId"]), user)
    if str(record.get("knowledgeId")) != str(draft.get("knowledgeId")):
        raise HTTPException(status_code=409, detail="guidance_scope_mismatch")
    return draft


def _get_learning_analysis_for_management(
    analysis_id: str,
    user: UserContext,
) -> dict[str, Any]:
    draft = store.get("learning_analysis_drafts", analysis_id)
    if not draft or not _is_active(draft):
        raise HTTPException(status_code=404, detail="learning_analysis_not_found")
    ensure_tenant_scope(user, str(draft.get("tenantId") or ""))
    knowledge = store.get("knowledges", str(draft.get("knowledgeId") or ""))
    if not knowledge or not _is_active(knowledge):
        raise HTTPException(status_code=404, detail="knowledge_not_found")
    ensure_tenant_scope(user, str(knowledge.get("tenantId") or ""))
    return draft


def _guidance_response(row: Mapping[str, Any], *, public: bool = False) -> GuidanceDraftResponse:
    response_keys = {
        "id",
        "recordId",
        "knowledgeId",
        "status",
        "modelId",
        "inputVersion",
        "summary",
        "learnerGuidance",
        "instructorGuidance",
        "assessments",
        "generatedAt",
        "publishedAt",
        "publishedByUserId",
        "updatedAt",
    }
    response = {key: row.get(key) for key in response_keys}
    if public:
        response["instructorGuidance"] = None
        response["publishedByUserId"] = None
    response["assessments"] = [
        dict(assessment)
        for assessment in row.get("assessments", [])
        if isinstance(assessment, Mapping)
    ]
    if public:
        response["assessments"] = [
            {
                **assessment,
                "instructorGuidance": "",
                "suggestedStatus": None,
            }
            for assessment in response["assessments"]
        ]
    return GuidanceDraftResponse.model_validate(response)


def _sort_guidance(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")), reverse=True)


def _resolve_guidance_model_id(knowledge: Mapping[str, Any]) -> str:
    plan = knowledge.get("interviewPlan")
    model_id = plan.get("modelId") if isinstance(plan, Mapping) else None
    if model_id in {"global.openai.gpt-5.6-luna", "global.openai.gpt-5.6-terra"}:
        return str(model_id)
    return settings.structured_interview_model_id


def _guidance_state_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "interviewProfile",
        "status",
        "fieldStates",
        "requirementStates",
        "processState",
        "applicabilityState",
        "contradictions",
        "openIssues",
        "stateVersion",
    }
    return {key: state.get(key) for key in allowed_keys if key in state}


def _valid_guidance_evidence_ids(
    messages: list[Mapping[str, Any]],
    objectives: list[Mapping[str, Any]],
) -> set[str]:
    evidence_ids = {
        str(message.get("id"))
        for message in messages
        if message.get("id")
    }
    for objective in objectives:
        evidence_ids.update(_string_list(objective.get("evidenceIds")))
    return evidence_ids


def _assessment_status(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return "not_evidenced"
    if value.get("needsClarification"):
        return "needs_follow_up"
    status = value.get("answerState") or value.get("status") or value.get("confirmationStatus")
    return _LEARNING_STATUS_MAP.get(str(status), "not_evidenced")


def _evidence_ids(value: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    evidence: list[str] = []
    for key in (
        "confirmationEvidenceTranscriptIds",
        "candidateEvidenceTranscriptIds",
        "evidenceTranscriptIds",
    ):
        for item in _string_list(value.get(key)):
            if item not in evidence:
                evidence.append(item)
    return evidence


def _reason(
    code: str,
    target_type: str,
    target_id: str | None,
    target_label: str | None,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return ReviewReason(
        code=code,
        targetType=target_type,
        targetId=target_id or None,
        targetLabel=target_label or None,
        evidenceIds=evidence_ids or [],
    ).model_dump()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _knowledge_profile(knowledge: Mapping[str, Any]) -> str | None:
    plan = knowledge.get("interviewPlan")
    if isinstance(plan, Mapping) and plan.get("profile"):
        return str(plan["profile"])
    return None


def _display_name(user_id: str | None) -> str | None:
    if not user_id:
        return None
    for context in DEV_TOKENS.values():
        if context.user_id == user_id:
            return context.display_name
    return user_id


def _is_active(row: Mapping[str, Any]) -> bool:
    return not row.get("deletedAt")


def _resolve_timezone(value: str | None) -> tuple[str, ZoneInfo]:
    normalized = str(value or "Asia/Tokyo").strip() or "Asia/Tokyo"
    try:
        return normalized, ZoneInfo(normalized)
    except (KeyError, ValueError):
        return "Asia/Tokyo", ZoneInfo("Asia/Tokyo")


def _date_key(value: Any, display_timezone: ZoneInfo) -> date | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(display_timezone).date()


def _date_in_range(value: date | None, date_from: date | None, date_to: date | None) -> bool:
    if value is None:
        return False if date_from or date_to else True
    if date_from and value < date_from:
        return False
    if date_to and value > date_to:
        return False
    return True
