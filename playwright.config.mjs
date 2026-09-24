import { defineConfig, devices } from "@playwright/test";
import { ensureVoiceFixtureWav } from "./tests/e2e/voice/fixtures/wavFixture.mjs";
import { readVoiceTestConfig } from "./tests/e2e/voice/config.mjs";

const voiceTestConfig = readVoiceTestConfig();
const audioFixture = ensureVoiceFixtureWav(
  voiceTestConfig.outputDir,
  "voice-fixture-1s.wav",
  voiceTestConfig.audioFixturePath,
);

export default defineConfig({
  testDir: "./tests/e2e/voice",
  testMatch: /.*\.spec\.mjs/,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: voiceTestConfig.maxRetries,
  workers: 1,
  outputDir: voiceTestConfig.outputDir,
  reporter: [
    ...(process.env.CI
      ? [["line"], ["json", { outputFile: voiceTestConfig.outputDir + "/playwright.json" }]]
      : [["list"]]),
    ["./tests/e2e/voice/reporting/voiceReportReporter.mjs", {
      outputDir: voiceTestConfig.outputDir,
      runId: voiceTestConfig.runId,
    }],
  ],
  use: {
    baseURL: voiceTestConfig.baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    permissions: ["microphone"],
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--use-file-for-fake-audio-capture=" + audioFixture,
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: voiceTestConfig.startLocalStack
    ? {
        command: "node tests/e2e/voice/drivers/startLocalStack.mjs",
        url: voiceTestConfig.baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }
    : undefined,
});
