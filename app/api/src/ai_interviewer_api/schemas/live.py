from typing import Literal

from pydantic import BaseModel, Field


class LiveSessionCreate(BaseModel):
    offer_sdp: str = Field(min_length=1)


class LiveSessionInfo(BaseModel):
    id: str


class LiveTransportInfo(BaseModel):
    type: Literal["webrtc"]
    sdp: str


class LiveSessionResponse(BaseModel):
    session: LiveSessionInfo
    transport: LiveTransportInfo
