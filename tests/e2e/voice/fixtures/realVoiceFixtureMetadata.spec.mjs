import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { test, expect } from "@playwright/test";

import {
  expectedFixtureRecords,
  scenarioVersion,
  validateRealVoiceFixtureMetadata,
} from "./realVoiceFixtureMetadata.mjs";

function writeFixtureDirectory() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-fixture-metadata-"));
  const records = expectedFixtureRecords("PARTIAL_ANSWER").map((record, index) => {
    const wav = Buffer.from(`test-wav-${index}`);
    const wavPath = path.join(directory, record.wav);
    fs.writeFileSync(wavPath, wav);
    return {
      ...record,
      sha256: crypto.createHash("sha256").update(wav).digest("hex"),
    };
  });
  fs.writeFileSync(
    path.join(directory, "real-voice-fixtures.json"),
    JSON.stringify({
      schemaVersion: 1,
      scenarioVersions: { PARTIAL_ANSWER: scenarioVersion("PARTIAL_ANSWER") },
      fixtures: { PARTIAL_ANSWER: records },
    }),
  );
  return directory;
}

test("real voice fixture metadata matches the current PARTIAL scenario", () => {
  const directory = writeFixtureDirectory();
  try {
    expect(validateRealVoiceFixtureMetadata({
      directory,
      behaviors: ["PARTIAL_ANSWER"],
    }).validated).toHaveLength(3);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("stale PARTIAL scenario text stops validation", () => {
  const directory = writeFixtureDirectory();
  try {
    const metadataPath = path.join(directory, "real-voice-fixtures.json");
    const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
    metadata.fixtures.PARTIAL_ANSWER[0].text = "古い回答です";
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    expect(() => validateRealVoiceFixtureMetadata({ directory })).toThrow(/STALE_AUDIO_FIXTURE/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("unregistered old PARTIAL WAV stops validation", () => {
  const directory = writeFixtureDirectory();
  try {
    fs.writeFileSync(path.join(directory, "PARTIAL_ANSWER-1-part1.wav"), "old");
    expect(() => validateRealVoiceFixtureMetadata({ directory })).toThrow(/STALE_AUDIO_FIXTURE/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
