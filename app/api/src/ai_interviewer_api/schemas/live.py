from typing import Literal

from pydantic import BaseModel, Field


class LiveSessionCreate(BaseModel):
    offer_sdp: str = Field(min_length=1)
    record_id: str | None = Field(default=None, min_length=1)


class LiveDelegationCreate(BaseModel):
    record_id: str = Field(min_length=1)
    delegation_id: str = Field(min_length=1, max_length=256)
    transcript: str = Field(min_length=1, max_length=20000)


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
