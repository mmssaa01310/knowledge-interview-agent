import { API_BASE_URL, ApiError } from "../../../lib/api";

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
  status: "updated" | "duplicate" | "completed" | "applied" | "stale";
  interviewState: Record<string, unknown>;
  structuredDraft: Record<string, string>;
  stateVersion?: number | null;
  currentQuestionId?: string | null;
};

export type GPTLiveStateUpdateResponse = {
  status: "applied" | "duplicate" | "stale" | "completed";
  stateVersion?: number | null;
  currentQuestionId?: string | null;
  coveredAspects: string[];
  missingAspects: string[];
};

export type LiveTranscriptFragment = {
  role: "user" | "assistant";
  text: string;
  start_ms?: number;
  end_ms?: number;
};

export type GPTLiveCaptureResponse = GPTLiveDelegationResponse & {
  checklist: {
    id: string;
    label: string;
    answer_state: string;
    answer_resolution?: string | null;
    candidate_answer?: string;
    needs_confirmation?: boolean;
    missing_required_items: string[];
  }[];
  processing?: boolean;
  completion?: { complete: boolean; closingRequired: boolean; missingRequiredTargets: unknown[]; pendingConfirmationTargets: unknown[]; unknownApplicabilityTopics: string[]; unresolvedContradictionIds: string[] };
};

export type GPTLiveCaptureReceipt = { status: "accepted"; revision: number };

export function getGPTLiveCaptureStatus(recordId: string, captureId: string) {
  return requestJson<GPTLiveCaptureResponse>(
    API_BASE_URL,
    `/api/live/captures/${encodeURIComponent(recordId)}?capture_id=${encodeURIComponent(captureId)}`,
  );
}

export function submitGPTLiveCapture(
  recordId: string, captureId: string, revision: number, fragments: LiveTranscriptFragment[],
) {
  return requestJson<GPTLiveCaptureReceipt>(API_BASE_URL, "/api/live/captures", {
    method: "POST",
    body: { record_id: recordId, capture_id: captureId, revision, fragments },
    signal: AbortSignal.timeout(15000),
    keepalive: true,
  });
}

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

export async function submitGPTLiveStateUpdate(
  recordId: string,
  payload: {
    delegation_id: string;
    call_id: string;
    turn_id: string;
    base_state_version: number;
    question_id: string;
    answer_status: "ANSWERED" | "PARTIAL" | "NOT_ANSWERED";
    covered_aspects: { aspect_id: string; evidence: string }[];
    missing_aspects: string[];
    evidence: { aspect_id: string; text: string }[];
    transcript?: string;
  },
  transcript = "",
  signal?: AbortSignal,
) {
  return requestJson<GPTLiveStateUpdateResponse>(
    API_BASE_URL,
    "/api/live/state-updates",
    {
      method: "POST",
      body: {
        record_id: recordId,
        ...payload,
        transcript: transcript || payload.transcript || "",
      },
      signal,
    },
  );
}
