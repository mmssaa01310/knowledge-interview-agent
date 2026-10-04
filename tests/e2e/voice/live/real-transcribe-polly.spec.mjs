import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import { fetchInterviewState, resetVoiceDemo } from "../drivers/apiDriver.mjs";
import { observePageNetwork } from "../drivers/appRoutes.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "../telemetry/telemetry.mjs";

const config = readVoiceTestConfig();
const recordUrl = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";

test.describe("voice E2E level 3: real Transcribe + Polly connection smoke", () => {
  test.skip(
    !config.realApiEnabled || config.realProvider !== "transcribe_polly",
    "Set VOICE_TEST_REAL_API_ENABLED=true and VOICE_TEST_REAL_PROVIDER=transcribe_polly",
  );

  test("opens one real Transcribe + Polly session without turn acceptance", async ({ page, request }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.requireRealApiOptIn();
    guard.startSession();
    guard.startTurn(5);
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: "real-transcribe-polly",
      scenario: "NORMAL",
      seed: config.seed,
      costGuard: guard,
    });
    const networkErrors = observePageNetwork(page, telemetry);
    let state = null;
    try {
      await resetVoiceDemo(request);
      await page.addInitScript(() => {
        window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
      });
      await page.goto(recordUrl);
      await page.selectOption("select", "transcribe_polly");
      await page.locator(".voice-conversation-button").click();
      await page.waitForFunction(
        () => document.querySelector(".voice-conversation-button")?.classList.contains("active"),
        null,
        { timeout: 30_000 },
      );
      telemetry.event("real_session_started", { provider: "transcribe_polly" });
      await page.waitForTimeout(5_000);
      state = await fetchInterviewState(request);
      expect(networkErrors).toEqual([]);
    } catch (error) {
      telemetry.error(error);
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        state,
        networkErrors,
      });
      throw error;
    }
  });
});
