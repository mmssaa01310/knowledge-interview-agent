const DEV_HEADERS = {
  "content-type": "application/json",
  "x-dev-token": process.env.VOICE_TEST_DEV_TOKEN || "dev-manager",
};

export function apiUrl(
  pathname,
  baseURL = process.env.VOICE_TEST_API_BASE_URL || "http://127.0.0.1:8001",
) {
  return baseURL + pathname;
}

export async function apiJson(request, pathname, options = {}) {
  const response = await request.fetch(apiUrl(pathname), {
    method: options.method ?? "GET",
    headers: { ...DEV_HEADERS, ...(options.headers ?? {}) },
    data: options.data,
  });
  if (!response.ok()) {
    throw new Error("API " + response.status() + " " + pathname + ": " + await response.text());
  }
  return response.json();
}

export async function resetVoiceDemo(request) {
  return apiJson(request, "/api/dev/voice-demo/reset", { method: "POST" });
}

export async function fetchInterviewState(
  request,
  recordId = "dev-voice-demo-record",
) {
  return apiJson(request, "/api/records/" + encodeURIComponent(recordId) + "/interview-state");
}

export async function fetchInterviewContext(
  request,
  recordId = "dev-voice-demo-record",
) {
  return apiJson(request, "/api/records/" + encodeURIComponent(recordId) + "/interview-context");
}

export function buildLiveAssessment({ field, transcript, stateVersion, turn }) {
  const questionId = "live-field-" + field.id;
  const requiredItems = field.questionPlan?.requiredItems ?? [];
  const covered = requiredItems.map((item) => ({
    aspect_id: item.itemId,
    evidence: transcript,
  }));
  return {
    delegation_id: "e2e-delegation-" + turn,
    call_id: "e2e-call-" + turn,
    turn_id: "e2e-turn-" + turn,
    base_state_version: Number(stateVersion ?? 0),
    question_id: questionId,
    answer_status: "ANSWERED",
    covered_aspects: covered,
    missing_aspects: [],
    evidence: covered.map(({ aspect_id, evidence }) => ({ aspect_id, text: evidence })),
    transcript,
  };
}

export async function applyLiveAssessment(request, recordId, payload) {
  return apiJson(request, "/api/live/state-updates", {
    method: "POST",
    data: { record_id: recordId, ...payload },
  });
}
