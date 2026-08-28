import { API_BASE_URL, ApiError } from "../../../lib/api";
import type { VoiceIceConfigResponse, VoiceSessionResponse } from "../types";

const VOICE_API_BASE_URL = "";
const DEV_AUTH_TOKEN = import.meta.env.VITE_DEV_TOKEN ?? "dev-manager";
const VOICE_RUNTIME_PROVIDER = import.meta.env.VITE_VOICE_RUNTIME_PROVIDER ?? "transcribe_polly";

type RequestOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
};

async function requestJson<T>(baseUrl: string, path: string, options: RequestOptions = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: options.method ?? "GET",
      headers: {
        "content-type": "application/json",
        "x-dev-token": DEV_AUTH_TOKEN,
        Authorization: `Bearer ${DEV_AUTH_TOKEN}`,
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "network_error";
    throw new ApiError(detail, { detail });
  }

  if (!response.ok) {
    const detail = await safeDetail(response);
    throw new ApiError(
      `${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`,
      { status: response.status, detail },
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

async function safeDetail(response: Response): Promise<string> {
  const responseText = await response.text();
  try {
    const parsed = JSON.parse(responseText) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : responseText;
  } catch {
    return responseText;
  }
}

export async function createVoiceSession(recordId: string, signal?: AbortSignal) {
  return requestJson<VoiceSessionResponse>(
    API_BASE_URL,
    `/api/records/${recordId}/voice-sessions`,
    { method: "POST", body: { provider: VOICE_RUNTIME_PROVIDER }, signal },
  );
}

export async function getVoiceIceConfig(voiceSessionId: string, signal?: AbortSignal) {
  return requestJson<VoiceIceConfigResponse>(
    VOICE_API_BASE_URL,
    `/voice/webrtc/${voiceSessionId}/ice-config`,
    { signal },
  );
}

export async function sendVoiceOffer(
  voiceSessionId: string,
  offer: RTCSessionDescriptionInit,
  signal?: AbortSignal,
) {
  return requestJson<{ type: "answer"; sdp: string }>(
    VOICE_API_BASE_URL,
    `/voice/webrtc/${voiceSessionId}/offer`,
    {
      method: "POST",
      body: {
        type: offer.type,
        sdp: offer.sdp,
      },
      signal,
    },
  );
}

export async function deleteVoicePeerConnection(
  voiceSessionId: string,
  reason = "client_requested",
  signal?: AbortSignal,
) {
  await requestJson<void>(
    VOICE_API_BASE_URL,
    `/voice/webrtc/${voiceSessionId}?reason=${encodeURIComponent(reason)}`,
    { method: "DELETE", signal },
  );
}
