import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  expectedFixtureRecords,
  fixtureMetadataPath,
  scenarioVersion,
  sha256File,
} from "./realVoiceFixtureMetadata.mjs";

const SAMPLE_RATE = 16_000;
const DEFAULT_OUTPUT_DIR = path.resolve(
  process.env.VOICE_TEST_AUDIO_FIXTURE_DIR
    || "test-results/voice-fixtures/robustness-inputs",
);

function wavFromPcm(pcm) {
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(SAMPLE_RATE * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

function synthesizePcm({ text, outputPath }) {
  const args = [
    "polly",
    "synthesize-speech",
    "--output-format",
    "pcm",
    "--engine",
    process.env.POLLY_FIXTURE_ENGINE || "neural",
    "--sample-rate",
    String(SAMPLE_RATE),
    "--voice-id",
    process.env.POLLY_FIXTURE_VOICE_ID || "Kazuha",
    "--language-code",
    process.env.POLLY_FIXTURE_LANGUAGE_CODE || "ja-JP",
    "--text",
    text,
    outputPath,
  ];
  execFileSync("aws", args, {
    env: process.env,
    stdio: "inherit",
  });
}

function loadExistingMetadata(outputDir) {
  const metadataPath = fixtureMetadataPath(outputDir);
  if (!fs.existsSync(metadataPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  } catch {
    return {};
  }
}

function removeBehaviorWavs(outputDir, behavior) {
  const prefix = `${behavior}-`;
  for (const name of fs.readdirSync(outputDir)) {
    if (name.startsWith(prefix) && name.toLowerCase().endsWith(".wav")) {
      fs.unlinkSync(path.join(outputDir, name));
    }
  }
}

function main() {
  const behavior = String(process.argv[2] || "PARTIAL_ANSWER").trim().toUpperCase();
  const outputDir = path.resolve(process.argv[3] || DEFAULT_OUTPUT_DIR);
  fs.mkdirSync(outputDir, { recursive: true });

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-voice-fixture-"));
  try {
    const expected = expectedFixtureRecords(behavior);
    const staged = expected.map((record) => {
      const rawPath = path.join(tempDir, `${record.fixtureId}.pcm`);
      const wavPath = path.join(tempDir, record.wav);
      synthesizePcm({ text: record.text, outputPath: rawPath });
      fs.writeFileSync(wavPath, wavFromPcm(fs.readFileSync(rawPath)));
      return {
        ...record,
        stagedWavPath: wavPath,
      };
    });
    // Do not remove a working fixture until every Polly request has
    // succeeded and all replacement WAVs are staged locally.
    removeBehaviorWavs(outputDir, behavior);
    const generated = staged.map(({ stagedWavPath, ...record }) => {
      const wavPath = path.join(outputDir, record.wav);
      fs.copyFileSync(stagedWavPath, wavPath);
      const stat = fs.statSync(wavPath);
      return {
        ...record,
        sha256: sha256File(wavPath),
        bytes: stat.size,
        sampleRate: SAMPLE_RATE,
        channels: 1,
        generatedAt: new Date().toISOString(),
      };
    });
    const existing = loadExistingMetadata(outputDir);
    const metadata = {
      schemaVersion: 1,
      generator: "aws-polly",
      voiceId: process.env.POLLY_FIXTURE_VOICE_ID || "Kazuha",
      languageCode: process.env.POLLY_FIXTURE_LANGUAGE_CODE || "ja-JP",
      sampleRate: SAMPLE_RATE,
      scenarioSource: "tests/e2e/voice/scenarios/scenarios.mjs",
      scenarioVersions: {
        ...(existing.scenarioVersions ?? {}),
        [behavior]: scenarioVersion(behavior),
      },
      fixtures: {
        ...(existing.fixtures ?? {}),
        [behavior]: generated,
      },
      generatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(
      fixtureMetadataPath(outputDir),
      `${JSON.stringify(metadata, null, 2)}\n`,
      "utf8",
    );
    console.log(`Generated ${generated.length} ${behavior} WAV fixture(s) in ${outputDir}`);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

main();
