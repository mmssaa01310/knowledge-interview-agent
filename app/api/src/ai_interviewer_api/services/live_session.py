from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.schemas.live import (
    LiveSessionCreate,
    LiveSessionInfo,
    LiveSessionResponse,
    LiveTransportInfo,
)


logger = logging.getLogger(__name__)

LIVE_MODEL = "gpt-live-1"
LIVE_INSTRUCTIONS = """You are a professional Japanese interviewer.

Speak naturally in Japanese.
Keep responses concise.
Allow the user time to think.
Do not interrupt because of a short pause.
Listen while the user is speaking.
If the user interrupts you, stop speaking and listen.
Ask one question at a time.
Avoid unnecessary confirmations."""


def create_live_session(
    payload: LiveSessionCreate,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> LiveSessionResponse:
    offer_sdp = payload.offer_sdp.strip()
    if not offer_sdp.startswith("v=0"):
        raise HTTPException(status_code=422, detail="invalid_sdp_offer")
    if not settings.openai_api_key.strip():
        raise HTTPException(status_code=503, detail="openai_api_key_missing")

    try:
        if client_factory is None:
            from openai import OpenAI

            client = OpenAI()
        else:
            client = client_factory()
        live = client.live.create(
            session={
                "model": LIVE_MODEL,
                "instructions": LIVE_INSTRUCTIONS,
                "audio": {
                    "output": {
                        "voice": "marin",
                    },
                },
            },
            transport={
                "type": "webrtc",
                "sdp": offer_sdp,
            },
        )
    except Exception as exc:
        logger.warning(
            "gpt_live_session_creation_failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="gpt_live_session_creation_failed",
        ) from exc

    session = _get_field(live, "session")
    transport = _get_field(live, "transport")
    session_id = _get_field(session, "id")
    transport_type = _get_field(transport, "type")
    transport_sdp = _get_field(transport, "sdp")
    if not all(isinstance(value, str) and value.strip() for value in (session_id, transport_sdp)):
        logger.warning("gpt_live_session_invalid_response")
        raise HTTPException(status_code=502, detail="gpt_live_invalid_response")
    if not transport_sdp.lstrip().startswith("v=0"):
        logger.warning("gpt_live_session_invalid_transport_sdp")
        raise HTTPException(status_code=502, detail="gpt_live_invalid_response")
    if transport_type != "webrtc":
        logger.warning("gpt_live_session_invalid_transport transport_type=%s", transport_type)
        raise HTTPException(status_code=502, detail="gpt_live_invalid_transport")

    return LiveSessionResponse(
        session=LiveSessionInfo(id=session_id),
        transport=LiveTransportInfo(type="webrtc", sdp=transport_sdp),
    )


def _get_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)
