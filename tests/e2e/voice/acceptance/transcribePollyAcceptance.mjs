import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { validateRealVoiceFixtureMetadata } from "../fixtures/realVoiceFixtureMetadata.mjs";

const repositoryRoot = process.cwd();
const runId = process.env.VOICE_TEST_ACCEPTANCE_RUN_ID
  || `transcribe-polly-${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}-${process.pid}`;
const outputDir = path.resolve(
  process.env.VOICE_TEST_ACCEPTANCE_OUTPUT_DIR
    || path.join("test-results", "voice-acceptance", runId),
);
const fixtureDirectory = path.resolve(
  process.env.VOICE_TEST_AUDIO_FIXTURE_DIR
    || "test-results/voice-fixtures/robustness-inputs",
);
const reportPath = path.join(outputDir, "acceptance-report.json");
const fixtureGenerator = path.join(
  repositoryRoot,
  "tests/e2e/voice/fixtures/generateRealVoiceFixtures.mjs",
);
const realPollyChunkProbe = path.join(
  repositoryRoot,
  "tests/e2e/voice/acceptance/realPollyChunkProbe.py",
);
const fixtureBehaviors = [
  "NORMAL",
  "PARTIAL_ANSWER",
  "PARTIAL_COMPLETION",
  "RECOVERY_NORMAL",
  "ASK_CLARIFICATION",
];

const report = {
  runId,
  startedAt: new Date().toISOString(),
  status: "RUNNING",
  mockE2E: { status: "PENDING" },
  realAws: { status: "PENDING" },
  artifacts: { outputDir, fixtureDirectory },
};

function writeReport() {
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

function run(command, args, env, { stdio = "inherit" } = {}) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    env,
    stdio,
    encoding: "utf8",
  });
  if (result.error) {
    return { exitCode: 1, error: result.error.message };
  }
  return { exitCode: result.status ?? 1, signal: result.signal ?? null };
}

function phaseEnvironment(outputPath, phaseRunId, overrides = {}) {
  return {
    ...process.env,
    ...overrides,
    VOICE_TEST_RUN_ID: phaseRunId,
    VOICE_TEST_OUTPUT_DIR: outputPath,
    VOICE_TEST_MAX_RETRIES: "0",
  };
}

function checkAwsIdentity() {
  const result = spawnSync("aws", ["sts", "get-caller-identity", "--output", "json"], {
    cwd: repositoryRoot,
    env: process.env,
    stdio: ["ignore", "ignore", "pipe"],
    encoding: "utf8",
  });
  if (!result.error && result.status === 0) {
    return { available: true };
  }

  const stderr = String(result.stderr ?? "").toLowerCase();
  if (result.error?.code === "ENOENT") {
    return {
      available: false,
      failureType: "AWS_CLI_UNAVAILABLE",
      detail: "AWS CLI was not found. Install AWS CLI v2 and rerun this command.",
    };
  }
  if (/unable to locate credentials|no credentials|could not find credentials/.test(stderr)) {
    return {
      available: false,
      failureType: "AWS_CREDENTIALS_UNAVAILABLE",
      detail: "No usable credentials were found for the selected AWS profile.",
    };
  }
  if (/expiredtoken|token has expired|sso session.*expired|credentials.*expired/.test(stderr)) {
    return {
      available: false,
      failureType: "AWS_CREDENTIALS_EXPIRED",
      detail: "The credentials for the selected AWS profile have expired. Refresh or sign in again.",
    };
  }
  if (/accessdenied|not authorized|access denied/.test(stderr)) {
    return {
      available: false,
      failureType: "AWS_ACCESS_DENIED",
      detail: "AWS denied the STS identity check for the selected profile.",
    };
  }
  if (/could not connect|connect timeout|could not resolve|name or service not known|network is unreachable|connection timed out|endpoint connection|proxy connection/.test(stderr)) {
    return {
      available: false,
      failureType: "AWS_NETWORK_OR_ENDPOINT_UNAVAILABLE",
      detail: "The AWS endpoint could not be reached from this environment. Check network access and retry outside restricted environments.",
    };
  }
  return {
    available: false,
    failureType: "AWS_STS_PREFLIGHT_FAILED",
    detail: "The AWS identity check failed for the selected profile. Check the AWS CLI error in your local terminal.",
  };
}

function ensureFixtures() {
  fs.mkdirSync(fixtureDirectory, { recursive: true });
  const generated = [];
  for (const behavior of fixtureBehaviors) {
    let valid = false;
    try {
      validateRealVoiceFixtureMetadata({
        directory: fixtureDirectory,
        behaviors: [behavior],
      });
      valid = true;
    } catch {
      const result = run(
        process.execPath,
        [fixtureGenerator, behavior, fixtureDirectory],
        process.env,
      );
      if (result.exitCode !== 0) {
        return { status: "FAIL", behavior, ...result };
      }
      generated.push(behavior);
    }
    if (!valid) {
      try {
        validateRealVoiceFixtureMetadata({
          directory: fixtureDirectory,
          behaviors: [behavior],
        });
      } catch (error) {
        return {
          status: "FAIL",
          behavior,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    }
  }
  return { status: "PASS", generated };
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  writeReport();

  const mockOutputDir = path.join(outputDir, "mock-e2e");
  const mockResult = run(
    "pnpm",
    ["exec", "playwright", "test", "tests/e2e/voice", "--project=chromium", "--workers=1", "--retries=0"],
    phaseEnvironment(mockOutputDir, `${runId}-mock`, {
      VOICE_TEST_REAL_API_ENABLED: "false",
      VOICE_TEST_REAL_PROVIDER: "gpt_live",
      VOICE_TEST_START_LOCAL_STACK: "true",
      VOICE_TEST_CAPTURE_TRANSCRIBE_LOGS: "true",
      VOICE_TEST_RUN_MODE: "acceptance",
    }),
  );
  report.mockE2E = {
    status: mockResult.exitCode === 0 ? "PASS" : "FAIL",
    ...mockResult,
    outputDir: mockOutputDir,
    apiLogPath: path.join(mockOutputDir, "api-service.log"),
    webLogPath: path.join(mockOutputDir, "web-service.log"),
  };
  writeReport();

  const awsIdentity = checkAwsIdentity();
  if (!awsIdentity.available) {
    report.realAws = {
      status: "FAIL",
      failureType: awsIdentity.failureType,
      detail: awsIdentity.detail,
      profile: process.env.AWS_PROFILE || process.env.AWS_DEFAULT_PROFILE || "default",
    };
  } else {
    const realOutputDir = path.join(outputDir, "real-aws");
    const pollyProbeOutputDir = path.join(realOutputDir, "polly-chunk-probe");
    const pollyProbeCommand = run(
      "uv",
      ["run", "--project", "app/voice", "python", realPollyChunkProbe, pollyProbeOutputDir],
      phaseEnvironment(pollyProbeOutputDir, `${runId}-polly-probe`, {
        APP_ENV: "test",
        UV_CACHE_DIR: process.env.UV_CACHE_DIR
          || path.join(os.tmpdir(), "kikiori-uv-cache"),
      }),
    );
    const pollyProbeReportPath = path.join(
      pollyProbeOutputDir,
      "polly-chunk-acceptance-report.json",
    );
    let pollyProbeReport = null;
    if (fs.existsSync(pollyProbeReportPath)) {
      try {
        pollyProbeReport = JSON.parse(fs.readFileSync(pollyProbeReportPath, "utf8"));
      } catch {
        pollyProbeReport = null;
      }
    }
    const pollyProbePassed = pollyProbeCommand.exitCode === 0
      && pollyProbeReport?.status === "PASS";

    const fixtureResult = ensureFixtures();
    report.realAws = {
      status: fixtureResult.status === "PASS" && pollyProbePassed ? "PENDING" : "FAIL",
      fixtures: fixtureResult,
      pollyChunkProbe: {
        status: pollyProbePassed ? "PASS" : "FAIL",
        ...pollyProbeCommand,
        reportPath: pollyProbeReportPath,
        report: pollyProbeReport,
      },
    };
    writeReport();

    if (fixtureResult.status === "PASS") {
      const realResult = run(
        "pnpm",
        [
          "exec",
          "playwright",
          "test",
          "tests/e2e/voice/live/transcribe-polly-acceptance.spec.mjs",
          "--project=chromium",
          "--workers=1",
          "--retries=0",
        ],
        phaseEnvironment(realOutputDir, `${runId}-real`, {
          VOICE_TEST_REAL_API_ENABLED: "true",
          VOICE_TEST_REAL_PROVIDER: "transcribe_polly",
          VOICE_TEST_TRANSCRIBE_POLLY_ACCEPTANCE: "true",
          VOICE_TEST_START_LOCAL_STACK: "true",
          VOICE_TEST_CAPTURE_TRANSCRIBE_LOGS: "true",
          VOICE_TEST_TRANSCRIBE_RECONNECT_AFTER_AUDIO_CHUNKS: "8",
          VOICE_TEST_AUDIO_FIXTURE_DIR: fixtureDirectory,
          VOICE_TEST_MAX_SESSIONS: "3",
          VOICE_TEST_MAX_TURNS_PER_SESSION: "4",
          VOICE_TEST_REAL_TURN_TIMEOUT_MS: "60000",
          VOICE_TEST_REAL_POLLY_DRAIN_TIMEOUT_MS: "30000",
          VOICE_TEST_RUN_MODE: "acceptance",
        }),
      );
      const acceptanceReportPath = path.join(
        realOutputDir,
        "acceptance",
        "transcribe-polly-report.json",
      );
      let acceptanceReport = null;
      if (fs.existsSync(acceptanceReportPath)) {
        try {
          acceptanceReport = JSON.parse(fs.readFileSync(acceptanceReportPath, "utf8"));
        } catch {
          acceptanceReport = null;
        }
      }
      const reportPassed = acceptanceReport?.status === "PASS"
        && acceptanceReport?.sessions?.length === 3;
      report.realAws = {
        status: realResult.exitCode === 0 && reportPassed && pollyProbePassed ? "PASS" : "FAIL",
        ...realResult,
        pollyChunkProbe: {
          status: pollyProbePassed ? "PASS" : "FAIL",
          ...pollyProbeCommand,
          reportPath: pollyProbeReportPath,
          report: pollyProbeReport,
        },
        acceptanceReportPath,
        acceptanceStatus: acceptanceReport?.status ?? "MISSING",
        fixtures: fixtureResult,
        outputDir: realOutputDir,
      };
    } else {
      report.realAws.status = "FAIL";
    }
  }

  report.completedAt = new Date().toISOString();
  report.status = report.mockE2E.status === "PASS" && report.realAws.status === "PASS"
    ? "PASS"
    : "FAIL";
  writeReport();
  console.log(`Transcribe + Polly acceptance: ${report.status}`);
  console.log(`Report: ${reportPath}`);
  if (report.status !== "PASS") process.exitCode = 1;
}

await main();
