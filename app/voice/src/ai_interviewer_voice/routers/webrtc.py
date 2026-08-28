from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import HTMLResponse

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.schemas.signaling import (
    AnswerResponse,
    IceConfigResponse,
    IceServerResponseItem,
    OfferRequest,
)
from ai_interviewer_voice.services.ice_server_service import IceServerService
from ai_interviewer_voice.services.runtime_factory import create_runtime
from ai_interviewer_voice.services.voice_session_service import VoiceSessionService
from ai_interviewer_voice.transports.webrtc.peer_connection import VoicePeerConnection
from ai_interviewer_voice.transports.webrtc.registry import DuplicatePeerConnectionError, PeerConnectionRegistry


router = APIRouter(prefix="/voice")

_voice_session_service = VoiceSessionService(
    api_base_url=settings.api_base_url,
    internal_api_token=settings.internal_api_token,
)
_ice_server_service = IceServerService(
    aws_region=settings.aws_region,
    kvs_turn_channel_arn=settings.kvs_turn_channel_arn,
    cache_ttl_seconds=settings.kvs_turn_cache_ttl_seconds,
)
_registry = PeerConnectionRegistry()


def _extract_bearer_token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="invalid_token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="invalid_token")
    return token


@router.get("/webrtc/{voice_session_id}/ice-config")
async def get_ice_config(
    voice_session_id: str,
    authorization: str | None = Header(default=None),
) -> IceConfigResponse:
    bearer_token = _extract_bearer_token(authorization)
    await _voice_session_service.authorize_session(voice_session_id, bearer_token=bearer_token)
    config = await _ice_server_service.get_ice_servers()
    return IceConfigResponse(
        iceServers=tuple(
            IceServerResponseItem(
                urls=server.urls,
                username=server.username,
                credential=server.credential,
            )
            for server in config.ice_servers
        ),
        expiresAt=config.expires_at.isoformat(),
    )


@router.post("/webrtc/{voice_session_id}/offer")
async def post_offer(
    voice_session_id: str,
    payload: OfferRequest,
    authorization: str | None = Header(default=None),
) -> AnswerResponse:
    bearer_token = _extract_bearer_token(authorization)
    session = await _voice_session_service.authorize_session(voice_session_id, bearer_token=bearer_token)
    existing = await _registry.get(voice_session_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="voice_session_already_connected")

    ice_config = await _ice_server_service.get_ice_servers()
    peer = VoicePeerConnection(
        session=session,
        bearer_token=bearer_token,
        runtime_factory=create_runtime,
        ice_servers=ice_config.ice_servers,
        voice_session_service=_voice_session_service,
        on_closed=_registry.remove,
        ice_gathering_timeout_seconds=settings.webrtc_ice_gathering_timeout_seconds,
        peer_disconnected_grace_seconds=settings.webrtc_peer_disconnected_grace_seconds,
        audio_input_queue_max_frames=settings.webrtc_audio_input_queue_max_frames,
        playback_buffer_target_ms=settings.webrtc_playback_buffer_target_ms,
        playback_buffer_retention_max_ms=settings.webrtc_playback_buffer_retention_max_ms,
        playback_preroll_ms=settings.webrtc_playback_preroll_ms,
        playback_short_underrun_ms=settings.webrtc_playback_short_underrun_ms,
        playback_drain_timeout_seconds=settings.webrtc_playback_drain_timeout_seconds,
    )
    try:
        await _registry.create(voice_session_id, peer)
    except DuplicatePeerConnectionError as exc:
        await peer.close(reason="duplicate_peer_connection", source="webrtc_offer_registry")
        raise HTTPException(status_code=409, detail="voice_session_already_connected") from exc

    try:
        sdp = await peer.apply_offer(payload.sdp, payload.type)
    except TimeoutError as exc:
        await _registry.remove(voice_session_id)
        await peer.close(reason="ice_gathering_timeout", source="webrtc_offer")
        raise HTTPException(status_code=504, detail="ice_gathering_timeout") from exc
    except Exception:
        await _registry.remove(voice_session_id)
        await peer.close(reason="offer_processing_failed", source="webrtc_offer")
        raise

    return AnswerResponse(sdp=sdp)


@router.delete("/webrtc/{voice_session_id}", status_code=204)
async def delete_peer_connection(
    voice_session_id: str,
    reason: str = "client_requested",
    authorization: str | None = Header(default=None),
) -> Response:
    bearer_token = _extract_bearer_token(authorization)
    await _voice_session_service.get_session(voice_session_id, bearer_token=bearer_token)
    peer = await _registry.remove(voice_session_id)
    if peer is not None:
        await peer.close(reason=reason, source="webrtc_delete_endpoint")
    return Response(status_code=204)


@router.get("/dev/webrtc", response_class=HTMLResponse)
async def get_webrtc_dev_page() -> HTMLResponse:
    if settings.app_env not in {"local", "test"}:
        raise HTTPException(status_code=404, detail="not_found")
    return HTMLResponse(_WEBRTC_DEV_PAGE)


_WEBRTC_DEV_PAGE = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Realtime Voice WebRTC PoC</title>
  <style>
    body { font-family: sans-serif; margin: 24px; }
    label { display: block; margin: 12px 0 4px; }
    input, button, textarea { font: inherit; width: 100%; box-sizing: border-box; }
    textarea { min-height: 220px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .actions { display: flex; gap: 12px; margin: 16px 0; }
    button { width: auto; padding: 8px 14px; }
  </style>
</head>
<body>
  <h1>Realtime Voice WebRTC PoC</h1>
  <div class="row">
    <div>
      <label>Bearer Token</label>
      <input id="token" type="password" autocomplete="off" />
    </div>
    <div>
      <label>Voice Session ID</label>
      <input id="voiceSessionId" type="text" autocomplete="off" />
    </div>
    <div>
      <label>Record ID</label>
      <input id="recordId" type="text" autocomplete="off" />
    </div>
  </div>
  <div class="actions">
    <button id="start">Start</button>
    <button id="stop">Stop</button>
  </div>
  <label>Connection State</label>
  <input id="connectionState" type="text" readonly />
  <div class="row">
    <div>
      <label>ICE Connection State</label>
      <input id="iceConnectionState" type="text" readonly />
    </div>
    <div>
      <label>Data Channel State</label>
      <input id="dataChannelState" type="text" readonly />
    </div>
    <div>
      <label>Microphone Track State</label>
      <input id="microphoneTrackState" type="text" readonly />
    </div>
    <div>
      <label>Remote Audio Track</label>
      <input id="remoteAudioTrackState" type="text" readonly />
    </div>
    <div>
      <label>Playback</label>
      <input id="playbackState" type="text" readonly />
    </div>
  </div>
  <label>Events</label>
  <textarea id="events" readonly></textarea>
  <audio id="remoteAudio" autoplay controls></audio>
  <script>
    let pc = null;
    let stream = null;
    let channel = null;
    const eventsEl = document.getElementById("events");
    const stateEl = document.getElementById("connectionState");
    const iceStateEl = document.getElementById("iceConnectionState");
    const dataStateEl = document.getElementById("dataChannelState");
    const micStateEl = document.getElementById("microphoneTrackState");
    const remoteTrackStateEl = document.getElementById("remoteAudioTrackState");
    const playbackStateEl = document.getElementById("playbackState");
    const audioEl = document.getElementById("remoteAudio");

    function log(line) {
      eventsEl.value += `${line}\\n`;
      eventsEl.scrollTop = eventsEl.scrollHeight;
    }

    async function waitForIceGatheringComplete(peer) {
      if (peer.iceGatheringState === "complete") {
        return;
      }
      await new Promise((resolve) => {
        const handler = () => {
          if (peer.iceGatheringState === "complete") {
            peer.removeEventListener("icegatheringstatechange", handler);
            resolve();
          }
        };
        peer.addEventListener("icegatheringstatechange", handler);
      });
    }

    async function start() {
      const token = document.getElementById("token").value.trim();
      const voiceSessionId = document.getElementById("voiceSessionId").value.trim();
      if (!token || !voiceSessionId) {
        log("token and voice session id are required");
        return;
      }

      const headers = { Authorization: `Bearer ${token}` };
      const iceResponse = await fetch(`/voice/webrtc/${voiceSessionId}/ice-config`, { headers });
      if (!iceResponse.ok) {
        log(`ice-config failed: ${iceResponse.status}`);
        return;
      }
      const iceConfig = await iceResponse.json();
      log("ice_config_loaded");
      pc = new RTCPeerConnection({ iceServers: iceConfig.iceServers });
      stateEl.value = pc.connectionState;
      iceStateEl.value = pc.iceConnectionState;

      pc.addEventListener("connectionstatechange", () => {
        stateEl.value = pc.connectionState;
        log(`connection_state ${pc.connectionState}`);
      });
      pc.addEventListener("iceconnectionstatechange", () => {
        iceStateEl.value = pc.iceConnectionState;
        log(`ice_connection_state ${pc.iceConnectionState}`);
      });
      pc.addEventListener("track", (event) => {
        const [remoteStream] = event.streams;
        if (remoteStream) {
          audioEl.srcObject = remoteStream;
          remoteTrackStateEl.value = "received";
          log("remote audio track received");
        }
      });

      channel = pc.createDataChannel("voice-events", { ordered: true });
      dataStateEl.value = channel.readyState;
      channel.addEventListener("open", () => {
        dataStateEl.value = channel.readyState;
        log("data channel open");
      });
      channel.addEventListener("close", () => {
        dataStateEl.value = channel.readyState;
        log("data channel closed");
      });
      channel.addEventListener("message", (event) => log(event.data));

      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      for (const track of stream.getAudioTracks()) {
        micStateEl.value = track.readyState;
        track.addEventListener("ended", () => {
          micStateEl.value = track.readyState;
        });
        pc.addTrack(track, stream);
      }
      audioEl.addEventListener("playing", () => {
        playbackStateEl.value = "playing";
        log("browser_audio_playback_started");
      });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc);

      const offerResponse = await fetch(`/voice/webrtc/${voiceSessionId}/offer`, {
        method: "POST",
        headers: {
          ...headers,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          type: pc.localDescription.type,
          sdp: pc.localDescription.sdp,
        }),
      });
      if (!offerResponse.ok) {
        log(`offer failed: ${offerResponse.status}`);
        return;
      }
      const answer = await offerResponse.json();
      await pc.setRemoteDescription(answer);
      log("remote answer applied");
    }

    async function stop() {
      const token = document.getElementById("token").value.trim();
      const voiceSessionId = document.getElementById("voiceSessionId").value.trim();
      if (token && voiceSessionId) {
        await fetch(`/voice/webrtc/${voiceSessionId}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}` },
        });
      }
      if (stream) {
        for (const track of stream.getTracks()) track.stop();
      }
      micStateEl.value = "ended";
      if (channel) channel.close();
      if (pc) pc.close();
      log("stopped");
    }

    document.getElementById("start").addEventListener("click", () => start().catch((error) => log(String(error))));
    document.getElementById("stop").addEventListener("click", () => stop().catch((error) => log(String(error))));
  </script>
</body>
</html>
"""
