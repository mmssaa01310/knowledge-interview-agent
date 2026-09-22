from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveSessionCreate(BaseModel):
    offer_sdp: str = Field(min_length=1)
    record_id: str | None = Field(default=None, min_length=1)


class LiveCoveredAspect(BaseModel):
    """One semantic aspect grounded in the participant's latest answer."""

    model_config = ConfigDict(extra="forbid")

    aspect_id: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=1200)


class LiveEvidence(BaseModel):
    """Evidence returned by the semantic evaluator, not generated wording."""

    model_config = ConfigDict(extra="forbid")

    aspect_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1200)


class LiveStateUpdateCreate(BaseModel):
    """Responses Delegation's state-store function payload.

    This contract intentionally contains no next action or question text.  The
    Live model remains the only component that decides what to say next.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    delegation_id: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    turn_id: str = Field(min_length=1, max_length=256)
    base_state_version: int = Field(ge=0)
    question_id: str = Field(min_length=1, max_length=256)
    answer_status: Literal["ANSWERED", "PARTIAL", "NOT_ANSWERED"]
    covered_aspects: list[LiveCoveredAspect] = Field(default_factory=list, max_length=12)
    missing_aspects: list[str] = Field(default_factory=list, max_length=12)
    evidence: list[LiveEvidence] = Field(default_factory=list, max_length=12)
    # The transcript comes from the browser capture, not from the evaluator's
    # semantic output.  It is optional because a delegation can be delivered
    # after the capture receipt and still be persisted using evidence.
    transcript: str = Field(default="", max_length=20000)


class LiveTranscriptFragment(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=20000)
    start_ms: float | None = Field(default=None, ge=0)
    end_ms: float | None = Field(default=None, ge=0)


class LiveCaptureCreate(BaseModel):
    record_id: str = Field(min_length=1)
    capture_id: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=1)
    fragments: list[LiveTranscriptFragment] = Field(min_length=1, max_length=128)


class LiveSessionInfo(BaseModel):
    id: str


class LiveTransportInfo(BaseModel):
    type: Literal["webrtc"]
    sdp: str


class LiveSessionResponse(BaseModel):
    session: LiveSessionInfo
    transport: LiveTransportInfo
