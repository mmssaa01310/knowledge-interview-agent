export async function installLocalApiRoutes(
  page,
  { sessionId = "e2e-live-session", apiEvents = [] } = {},
) {
  await page.route("**/api/live/sessions", async (route) => {
    apiEvents.push({ type: "live_session_created", timestampMs: Date.now() });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session: { id: sessionId },
        transport: { type: "webrtc", sdp: "v=0\r\n" },
      }),
    });
  });
  await page.route("**/api/live/captures**", async (route) => {
    const request = route.request();
    apiEvents.push({ type: "capture", method: request.method(), timestampMs: Date.now() });
    if (request.method() === "POST") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ status: "accepted", revision: 1 }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        processing: false,
        completion: {
          complete: false,
          closingRequired: false,
          missingRequiredTargets: [],
          pendingConfirmationTargets: [],
          unknownApplicabilityTopics: [],
          unresolvedContradictionIds: [],
        },
      }),
    });
  });
}

export function observePageNetwork(page, telemetry) {
  const errors = [];
  page.on("requestfailed", (request) => {
    const item = {
      url: request.url(),
      failure: request.failure()?.errorText || "unknown",
    };
    errors.push(item);
    telemetry.error(item.failure);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      const item = { url: response.url(), status: response.status() };
      errors.push(item);
      telemetry.event("api_5xx", item);
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") telemetry.consoleError(message.text());
    const text = message.text();
    if (text.startsWith("gpt_live_") || text.startsWith("voice_")) {
      telemetry.event("browser_log", { text });
    }
  });
  return errors;
}
