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
  const now = () => Date.now();

  page.on("request", (request) => {
    telemetry.networkEvent({
      direction: "request",
      method: request.method(),
      url: request.url(),
      resourceType: request.resourceType(),
      timestampMs: now(),
    });
  });

  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText || "unknown";
    const expectedCancellation = /ERR_ABORTED|aborted/i.test(failure);
    const item = {
      url: request.url(),
      method: request.method(),
      failure,
      expectedCancellation,
      timestampMs: now(),
    };
    telemetry.networkEvent({
      direction: "request_failed",
      method: item.method,
      url: item.url,
      resourceType: request.resourceType(),
      failure: item.failure,
      expectedCancellation,
      timestampMs: item.timestampMs,
    });
    if (expectedCancellation) {
      telemetry.event("expected_request_cancellation", item);
    } else {
      errors.push(item);
      telemetry.error(item.failure);
    }
  });
  page.on("response", (response) => {
    const item = {
      url: response.url(),
      method: response.request().method(),
      resourceType: response.request().resourceType(),
      status: response.status(),
      timestampMs: now(),
    };
    telemetry.networkEvent({ direction: "response", ...item });
    if (response.status() >= 500) {
      const error = { url: item.url, status: item.status, timestampMs: item.timestampMs };
      errors.push(error);
      telemetry.event("api_5xx", error);
    }
  });
  page.on("console", (message) => {
    const text = message.text();
    const location = message.location();
    telemetry.consoleLog({
      type: message.type(),
      text,
      location: {
        url: location.url || null,
        lineNumber: location.lineNumber ?? null,
        columnNumber: location.columnNumber ?? null,
      },
      timestampMs: now(),
    });
    if (message.type() === "error") telemetry.consoleError(text);
    if (text.startsWith("gpt_live_") || text.startsWith("voice_")) {
      telemetry.event("browser_log", { text });
    }
    if (/data[_-]channel[_-]error|websocket.*error|transport\.closed/i.test(text)) {
      telemetry.dataChannelError({
        type: "browser_transport_log",
        message: text,
        timestampMs: now(),
      });
    }
  });
  page.on("pageerror", (error) => {
    const message = error instanceof Error ? error.message : String(error);
    telemetry.consoleLog({ type: "pageerror", text: message, timestampMs: now() });
    telemetry.consoleError(message);
  });
  page.on("websocket", (websocket) => {
    const url = websocket.url();
    telemetry.networkEvent({
      direction: "websocket_open",
      url,
      timestampMs: now(),
    });
    websocket.on("socketerror", (error) => {
      const message = String(error);
      const item = { type: "websocket_error", message, url, timestampMs: now() };
      errors.push(item);
      telemetry.dataChannelError(item);
      telemetry.networkEvent({
        direction: "websocket_error",
        url,
        failure: message,
        timestampMs: item.timestampMs,
      });
    });
    websocket.on("close", () => {
      telemetry.networkEvent({
        direction: "websocket_close",
        url,
        timestampMs: now(),
      });
    });
  });
  return errors;
}
