import fs from "node:fs";
import path from "node:path";

import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import {
  compareLevel2Level3,
  readExplorationSummary,
} from "../supervisor/level3Promotion.mjs";
import {
  createLevel3Config,
  assertLevel3Fixture,
  level3ReplayCommand,
  runRealGPTLiveCandidate,
} from "./level3RealRunner.mjs";
import { writeEvidencePackage } from "../telemetry/telemetry.mjs";

const config = readVoiceTestConfig();
const defaultSummaryPath = path.join(config.outputDir, "exploration-summary.json");
const summaryPath = config.level2SummaryPath || defaultSummaryPath;
let promotionInput = null;
try {
  if (fs.existsSync(summaryPath)) promotionInput = readExplorationSummary(summaryPath);
} catch {
  promotionInput = null;
}

test.describe("voice E2E level 3: Jev promotion to real GPT Live", () => {
  test.describe.configure({ retries: 0 });
  test.skip(
    !config.realApiEnabled || config.realProvider !== "gpt_live",
    "Set VOICE_TEST_REAL_API_ENABLED=true and VOICE_TEST_REAL_PROVIDER=gpt_live",
  );
  test.skip(
    !config.runBrowserAudio,
    "Set VOICE_TEST_RUN_BROWSER_AUDIO=true to run browser audio tests",
  );

  test("revalidates bounded Level 2 candidates with real GPT Live", async ({ page, request }, testInfo) => {
    test.setTimeout(Math.max(
      120_000,
      30_000 + config.maxLevel3Sessions * 120_000,
    ));
    if (!promotionInput) {
      test.skip(`Level 2 summary was not found: ${summaryPath}`);
    }
    if (!config.audioFixturePath) {
      test.skip(
        "Level 3 requires VOICE_TEST_AUDIO_FIXTURE with prerecorded speech; generated tone is not used.",
      );
    }
    assertLevel3Fixture(config.audioFixturePath);
    const candidates = promotionInput.candidates.slice(0, config.maxLevel3Sessions);
    if (candidates.length === 0) {
      test.skip("Level 2 produced no promotion candidate; clean NORMAL-only exploration is not promoted.");
    }
    expect(promotionInput.summary.seed).toBe(config.seed);

    const level3Config = createLevel3Config(config, testInfo.outputPath("level3"));
    const guard = new VoiceCostGuard(level3Config);
    guard.requireRealApiOptIn();
    const results = [];
    const failures = [];

    for (let index = 0; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      guard.startSession();
      try {
        const result = await runRealGPTLiveCandidate({
          page,
          request,
          testInfo,
          config: level3Config,
          guard,
          candidate,
          sessionIndex: index + 1,
          audioFixturePath: config.audioFixturePath,
        });
        const comparison = compareLevel2Level3(candidate, result.turn);
        const level3Failure = result.hardGate.status === "FAIL"
          || result.networkErrors.length > 0
          || result.telemetry.errors.length > 0;
        const record = {
          sessionIndex: index + 1,
          sessionId: result.sessionId,
          seed: candidate.seed,
          behavior: candidate.behavior,
          candidate,
          hardGate: {
            status: result.hardGate.status,
            codes: result.hardGate.codes,
            expectationResult: result.hardGate.expectationResult,
          },
          transcript: result.transcript,
          assistantResponse: result.assistantResponse,
          state: result.state?.interviewState ?? null,
          timing: result.realTiming,
          comparison,
          status: level3Failure ? "FAIL" : "PASS",
        };
        results.push(record);
        if (level3Failure) {
          failures.push(record);
          await writeEvidencePackage({
            testInfo,
            page,
            telemetry: result.telemetry,
            state: result.state,
            networkErrors: result.networkErrors,
            candidate,
            comparison,
            assistantResponse: result.assistantResponse,
            audioFixturePath: config.audioFixturePath,
            evidenceDir: testInfo.outputPath("level3", `session-${index + 1}`, "evidence"),
            replayCommand: level3ReplayCommand({ config, candidate }),
          });
        }
      } catch (error) {
        failures.push({
          sessionIndex: index + 1,
          seed: candidate.seed,
          behavior: candidate.behavior,
          candidate,
          status: "ERROR",
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }

    const level3Summary = {
      level: 3,
      audioInputMode: "fake_microphone_wav",
      transcriptSource: "gpt_live",
      liveApiUsed: true,
      sourceLevel2Summary: promotionInput.path,
      seed: config.seed,
      limits: {
        maxLevel3Sessions: config.maxLevel3Sessions,
        maxTurnsPerSession: config.maxLevel3TurnsPerSession,
        maxRetries: config.maxLevel3Retries,
      },
      candidatesConsidered: candidates.length,
      results,
      failures,
      comparisonCounts: {
        level2PassToLevel3Fail: results.filter(
          (item) => item.comparison?.classification === "LEVEL2_PASS_LEVEL3_FAIL",
        ).length,
        level2FailToLevel3Fail: results.filter(
          (item) => item.comparison?.classification === "LEVEL2_FAIL_LEVEL3_FAIL",
        ).length,
        matches: results.filter((item) => item.comparison?.structuralMatch).length,
      },
      costGuard: guard.snapshot(),
    };
    const summaryOutputPath = testInfo.outputPath("level3", "level3-summary.json");
    fs.writeFileSync(summaryOutputPath, JSON.stringify(level3Summary, null, 2), "utf8");
    expect(guard.snapshot().sessions).toBeLessThanOrEqual(config.maxLevel3Sessions);
    expect(guard.snapshot().turns).toBeLessThanOrEqual(
      config.maxLevel3Sessions * config.maxLevel3TurnsPerSession,
    );
    expect(failures, `Level 3 failures are recorded under ${summaryOutputPath}`).toEqual([]);
  });
});

