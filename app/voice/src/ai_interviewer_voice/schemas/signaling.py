from __future__ import annotations

from pydantic import BaseModel, Field


class OfferRequest(BaseModel):
    type: str
    sdp: str


class AnswerResponse(BaseModel):
    type: str = "answer"
    sdp: str


class IceServerResponseItem(BaseModel):
    urls: tuple[str, ...]
    username: str | None = None
    credential: str | None = None


class IceConfigResponse(BaseModel):
    iceServers: tuple[IceServerResponseItem, ...]
    expiresAt: str


class ConnectionStateResponse(BaseModel):
    voiceSessionId: str
    state: str
    detail: dict = Field(default_factory=dict)
