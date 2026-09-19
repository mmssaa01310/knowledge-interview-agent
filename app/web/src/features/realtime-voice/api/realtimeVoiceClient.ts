import { API_BASE_URL, ApiError } from "../../../lib/api";
import type { LegacyVoiceProvider, VoiceIceConfigResponse, VoiceSessionResponse } from "../types";

const VOICE_API_BASE_URL = "";
const DEV_AUTH_TOKEN = import.meta.env.VITE_DEV_TOKEN ?? "dev-manager";
export const VOICE_RUNTIME_PROVIDER = import.meta.env.VITE_VOICE_RUNTIME_PROVIDER ?? "transcribe_polly";

export type GPTLiveSessionResponse = {
  session: {
    id: string;
  };
  transport: {
    type: "webrtc";
    sdp: string;
  };
};

export type GPTLiveDelegationResponse = {
  status: "updated" | "duplicate" | "completed";
  interviewState: Record<string, unknown>;
  structuredDraft: Record<string, string>;
  stateVersion?: number | null;
};

type RequestOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  keepalive?: boolean;
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
      keepalive: options.keepalive,
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

export async function createVoiceSession(
  recordId: string,
  provider: LegacyVoiceProvider = getDefaultLegacyVoiceProvider(),
  signal?: AbortSignal,
) {
  return requestJson<VoiceSessionResponse>(
    API_BASE_URL,
    `/api/records/${recordId}/voice-sessions`,
    { method: "POST", body: { provider }, signal },
  );
}

export function getDefaultLegacyVoiceProvider(): LegacyVoiceProvider {
  if (VOICE_RUNTIME_PROVIDER === "nova_sonic" || VOICE_RUNTIME_PROVIDER === "openai_realtime") {
    return VOICE_RUNTIME_PROVIDER;
  }
  return "transcribe_polly";
}

export async function createGPTLiveSession(
  offerSdp: string,
  recordId?: string,
  signal?: AbortSignal,
) {
  return requestJson<GPTLiveSessionResponse>(
    API_BASE_URL,
    "/api/live/sessions",
    {
      method: "POST",
      body: {
        offer_sdp: offerSdp,
        ...(recordId ? { record_id: recordId } : {}),
      },
      signal,
    },
  );
}

export async function submitGPTLiveDelegation(
  recordId: string,
  delegationId: string,
  transcript: string,
  signal?: AbortSignal,
) {
  return requestJson<GPTLiveDelegationResponse>(
    API_BASE_URL,
    "/api/live/delegations",
    {
      method: "POST",
      body: {
        record_id: recordId,
        delegation_id: delegationId,
        transcript,
      },
      signal,
    },
  );
}

export async function sendOpenAIRealtimeOffer(
  voiceSessionId: string,
  offer: RTCSessionDescriptionInit,
  signal?: AbortSignal,
) {
  let response: Response;
  console.info("openai_realtime_call_request", {
    voice_session_id: voiceSessionId,
    offer_type: offer.type,
  });
  try {
    response = await fetch(`${VOICE_API_BASE_URL}/voice/webrtc/${voiceSessionId}/openai-offer`, {
      method: "POST",
      headers: {
        "content-type": "application/sdp",
        "x-dev-token": DEV_AUTH_TOKEN,
        Authorization: `Bearer ${DEV_AUTH_TOKEN}`,
      },
      body: offer.sdp ?? "",
      signal,
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
  return { type: "answer" as const, sdp: await response.text() };
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
  keepalive = false,
) {
  await requestJson<void>(
    VOICE_API_BASE_URL,
    `/voice/webrtc/${voiceSessionId}?reason=${encodeURIComponent(reason)}`,
    { method: "DELETE", signal, keepalive },
  );
}
