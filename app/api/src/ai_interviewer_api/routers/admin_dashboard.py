from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.schemas.dashboard import (
    AdminDashboardResponse,
    GuidanceDraftResponse,
    GuidanceUpdateRequest,
    LearningAnalysisDraftResponse,
    LearningAnalysisRequest,
    LearningAnalysisUpdateRequest,
)
from ai_interviewer_api.services.admin_dashboard import (
    GuidanceGenerationError,
    LearningAnalysisGenerationError,
    build_admin_dashboard,
    generate_guidance_draft,
    generate_learning_analysis,
    list_learning_analyses,
    list_guidance_for_record,
    publish_guidance_draft,
    review_learning_analysis,
    unpublish_guidance_draft,
    update_learning_analysis,
    update_guidance_draft,
)

router = APIRouter(prefix="/api")


@router.get("/admin/dashboard", response_model=AdminDashboardResponse)
def get_admin_dashboard(
    date_from: date | None = Query(default=None, alias="dateFrom"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    knowledge_id: str | None = Query(default=None, alias="knowledgeId"),
    profile: str | None = Query(default=None),
    record_status: str | None = Query(default=None, alias="recordStatus"),
    limit: int = Query(default=100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
) -> AdminDashboardResponse:
    return build_admin_dashboard(
        user,
        date_from=date_from,
        date_to=date_to,
        knowledge_id=knowledge_id,
        profile=profile,
        record_status=record_status,
        limit=limit,
    )


@router.post("/admin/learning-analysis", response_model=LearningAnalysisDraftResponse)
def create_learning_analysis(
    payload: LearningAnalysisRequest,
    user: UserContext = Depends(get_current_user),
) -> LearningAnalysisDraftResponse:
    try:
        return generate_learning_analysis(user, payload)
    except LearningAnalysisGenerationError as exc:
        raise HTTPException(status_code=502, detail="learning_analysis_generation_failed") from exc


@router.get("/admin/learning-analysis", response_model=list[LearningAnalysisDraftResponse])
def get_learning_analyses(
    knowledge_id: str | None = Query(default=None, alias="knowledgeId"),
    limit: int = Query(default=20, ge=1, le=100),
    user: UserContext = Depends(get_current_user),
) -> list[LearningAnalysisDraftResponse]:
    return list_learning_analyses(user, knowledge_id=knowledge_id, limit=limit)


@router.patch("/admin/learning-analysis/{analysis_id}", response_model=LearningAnalysisDraftResponse)
def update_learning_analysis_draft(
    analysis_id: str,
    payload: LearningAnalysisUpdateRequest,
    user: UserContext = Depends(get_current_user),
) -> LearningAnalysisDraftResponse:
    return update_learning_analysis(analysis_id, payload, user)


@router.post("/admin/learning-analysis/{analysis_id}/review", response_model=LearningAnalysisDraftResponse)
def review_learning_analysis_draft(
    analysis_id: str,
    user: UserContext = Depends(get_current_user),
) -> LearningAnalysisDraftResponse:
    return review_learning_analysis(analysis_id, user)


@router.post("/admin/records/{record_id}/guidance", response_model=GuidanceDraftResponse)
def create_guidance_draft(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> GuidanceDraftResponse:
    try:
        return generate_guidance_draft(record_id, user)
    except GuidanceGenerationError as exc:
        raise HTTPException(status_code=502, detail="guidance_generation_failed") from exc


@router.get("/admin/records/{record_id}/guidance", response_model=list[GuidanceDraftResponse])
def get_management_guidance(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> list[GuidanceDraftResponse]:
    return list_guidance_for_record(record_id, user)


@router.get("/records/{record_id}/guidance", response_model=list[GuidanceDraftResponse])
def get_public_guidance(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> list[GuidanceDraftResponse]:
    return list_guidance_for_record(record_id, user, public=True)


@router.patch("/admin/guidance/{draft_id}", response_model=GuidanceDraftResponse)
def update_guidance(
    draft_id: str,
    payload: GuidanceUpdateRequest,
    user: UserContext = Depends(get_current_user),
) -> GuidanceDraftResponse:
    return update_guidance_draft(draft_id, payload, user)


@router.post("/admin/guidance/{draft_id}/publish", response_model=GuidanceDraftResponse)
def publish_guidance(
    draft_id: str,
    user: UserContext = Depends(get_current_user),
) -> GuidanceDraftResponse:
    return publish_guidance_draft(draft_id, user)


@router.post("/admin/guidance/{draft_id}/unpublish", response_model=GuidanceDraftResponse)
def unpublish_guidance(
    draft_id: str,
    user: UserContext = Depends(get_current_user),
) -> GuidanceDraftResponse:
    return unpublish_guidance_draft(draft_id, user)
