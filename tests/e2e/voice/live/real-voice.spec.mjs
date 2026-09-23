import { test } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import { ensureVoiceFixtureWav } from "../fixtures/wavFixture.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "../telemetry/telemetry.mjs";
import {
  observePageNetwork,
} from "../drivers/appRoutes.mjs";

const config = readVoiceTestConfig();
const recordUrl = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";

test.describe("voice E2E level 3: real provider opt-in", () => {
  test.skip(
    !config.realApiEnabled || config.realProvider !== "gpt_live",
    "Set VOICE_TEST_REAL_API_ENABLED=true and VOICE_TEST_REAL_PROVIDER=gpt_live",
  );

  test("starts one bounded real GPT Live session", async ({ page }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.requireRealApiOptIn();
    guard.startSession();
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: "real-gpt-live",
      scenario: "NORMAL",
      seed: config.seed,
      costGuard: guard,
    });
    const networkErrors = observePageNetwork(page, telemetry);
    try {
      ensureVoiceFixtureWav(config.outputDir);
      await page.addInitScript(() => {
        window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
      });
      await page.goto(recordUrl);
      await page.selectOption("select", "gpt_live");
      await page.locator(".voice-conversation-button").click();
      await page.waitForFunction(
        () => document.querySelector(".voice-conversation-button")?.classList.contains("active"),
        null,
        { timeout: 30_000 },
      );
      guard.startTurn(2);
      telemetry.event("real_session_started");
      await page.waitForTimeout(2_000);
    } catch (error) {
      telemetry.error(error);
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        networkErrors,
      });
      throw error;
    }
  });
});
