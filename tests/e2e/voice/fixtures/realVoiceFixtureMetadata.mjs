import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  REAL_VOICE_FIXTURE_TARGETS,
  REAL_VOICE_FIXTURE_TEXTS,
} from "../scenarios/scenarios.mjs";

export const REAL_VOICE_FIXTURE_METADATA_FILENAME = "real-voice-fixtures.json";
export const STALE_AUDIO_FIXTURE = "STALE_AUDIO_FIXTURE";

const FIXTURE_ID_PREFIXES = Object.freeze({
  PARTIAL_ANSWER: "partial-role-only-v2",
});

function normalizeBehavior(behavior) {
  return String(behavior ?? "").trim().toUpperCase();
}

function fixtureIdPrefix(behavior) {
  const normalized = normalizeBehavior(behavior);
  return FIXTURE_ID_PREFIXES[normalized]
    ?? normalized.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

export function scenarioVersion(behavior) {
  const normalized = normalizeBehavior(behavior);
  const texts = REAL_VOICE_FIXTURE_TEXTS[normalized];
  if (!Array.isArray(texts) || !texts.length) {
    throw new Error(`Unknown real voice fixture behavior: ${behavior}`);
  }
  return crypto.createHash("sha256")
    .update(JSON.stringify({ behavior: normalized, texts }))
    .digest("hex");
}

export function expectedFixtureRecords(behavior) {
  const normalized = normalizeBehavior(behavior);
  const texts = REAL_VOICE_FIXTURE_TEXTS[normalized];
  if (!Array.isArray(texts) || !texts.length) {
    throw new Error(`Unknown real voice fixture behavior: ${behavior}`);
  }
  const version = scenarioVersion(normalized);
  const prefix = fixtureIdPrefix(normalized);
  return texts.map((text, variant) => ({
    ...(REAL_VOICE_FIXTURE_TARGETS[normalized]
      ? {
        targetField: REAL_VOICE_FIXTURE_TARGETS[normalized].fieldId,
        targetAspects: [...REAL_VOICE_FIXTURE_TARGETS[normalized].aspectIds],
        expectedMissingAspects: [
          ...REAL_VOICE_FIXTURE_TARGETS[normalized].expectedMissingAspectIds,
        ],
      }
      : {}),
    behavior: normalized,
    variant,
    fixtureId: `${prefix}-${variant + 1}`,
    text,
    generatedFromScenarioVersion: version,
    wav: `${normalized}-${variant + 1}.wav`,
  }));
}

export function sha256File(filePath) {
  return crypto.createHash("sha256")
    .update(fs.readFileSync(filePath))
    .digest("hex");
}

function stale(message) {
  return new Error(`${STALE_AUDIO_FIXTURE}: ${message}`);
}

function readMetadata(directory) {
  const metadataPath = path.join(directory, REAL_VOICE_FIXTURE_METADATA_FILENAME);
  if (!fs.existsSync(metadataPath)) {
    throw stale(`metadata is missing: ${metadataPath}`);
  }
  try {
    return {
      metadataPath,
      metadata: JSON.parse(fs.readFileSync(metadataPath, "utf8")),
    };
  } catch (error) {
    throw stale(`metadata is invalid: ${metadataPath} (${error.message})`);
  }
}

function wavNamesForBehavior(directory, behavior) {
  const prefix = `${normalizeBehavior(behavior)}-`;
  return fs.readdirSync(directory)
    .filter((name) => name.startsWith(prefix) && name.toLowerCase().endsWith(".wav"))
    .sort();
}

/**
 * Validate scenario text, generated metadata, and the actual WAV bytes before
 * a real voice API test starts. The metadata is intentionally side-by-side
 * with the externally supplied fixture directory so an old ignored WAV cannot
 * silently survive a scenario text change.
 */
export function validateRealVoiceFixtureMetadata({
  directory,
  behaviors = ["PARTIAL_ANSWER"],
} = {}) {
  if (!directory || !fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    throw stale(`fixture directory is missing: ${directory ?? "(not configured)"}`);
  }
  const { metadataPath, metadata } = readMetadata(directory);
  if (metadata?.schemaVersion !== 1 || typeof metadata?.fixtures !== "object") {
    throw stale(`unsupported metadata schema: ${metadataPath}`);
  }

  const validated = [];
  for (const behavior of [...new Set(behaviors.map(normalizeBehavior))]) {
    const expected = expectedFixtureRecords(behavior);
    const actual = metadata.fixtures?.[behavior];
    if (metadata.scenarioVersions?.[behavior] !== scenarioVersion(behavior)) {
      throw stale(`${behavior} scenario version is stale`);
    }
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      throw stale(`${behavior} metadata does not match the current scenario variants`);
    }
    const expectedNames = new Set();
    for (const expectedRecord of expected) {
      const record = actual.find((candidate) => candidate?.variant === expectedRecord.variant);
      if (!record) {
        throw stale(`${behavior} variant ${expectedRecord.variant + 1} is missing from metadata`);
      }
      for (const field of [
        "behavior",
        "fixtureId",
        "text",
        "generatedFromScenarioVersion",
        "wav",
        "targetField",
        "targetAspects",
        "expectedMissingAspects",
      ]) {
        if (record[field] !== expectedRecord[field]) {
          if (Array.isArray(record[field]) && Array.isArray(expectedRecord[field])
            && JSON.stringify(record[field]) === JSON.stringify(expectedRecord[field])) {
            continue;
          }
          throw stale(`${behavior} variant ${expectedRecord.variant + 1} has stale ${field}`);
        }
      }
      const wavPath = path.resolve(directory, record.wav);
      if (path.dirname(wavPath) !== path.resolve(directory) || !fs.existsSync(wavPath)) {
        throw stale(`${behavior} variant ${expectedRecord.variant + 1} WAV is missing`);
      }
      if (record.sha256 !== sha256File(wavPath)) {
        throw stale(`${behavior} variant ${expectedRecord.variant + 1} WAV bytes do not match metadata`);
      }
      expectedNames.add(record.wav);
      validated.push({ ...record, wavPath });
    }
    const actualNames = wavNamesForBehavior(directory, behavior);
    const unexpected = actualNames.filter((name) => !expectedNames.has(name));
    if (unexpected.length) {
      throw stale(`${behavior} contains unregistered WAV fixture(s): ${unexpected.join(", ")}`);
    }
  }
  return { metadataPath, validated };
}

export function fixtureMetadataPath(directory) {
  return path.join(directory, REAL_VOICE_FIXTURE_METADATA_FILENAME);
}
