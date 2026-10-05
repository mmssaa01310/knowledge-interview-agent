import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { VoiceTelemetry } from "./telemetry.mjs";

test("turn telemetry preserves final transcript and field progression counts", () => {
  const outputDir = fs.mkdtempSync(path.join(os.tmpdir(), "voice-telemetry-"));
  try {
    const telemetry = new VoiceTelemetry({
      outputDir,
      sessionId: "session-1",
      scenario: "acceptance",
      seed: 1,
      transcriptSource: "transcribe_polly",
    });
    telemetry.recordTurn({
      turn: 1,
      transcription: {
        finalCount: 1,
        finalText: "回答です。",
      },
      interview: {
        currentFieldBeforeId: "field-before",
        currentFieldAfterId: "field-after",
        expectedFieldAfterId: "field-after",
      },
    });

    expect(telemetry.snapshot().turns[0]).toMatchObject({
      transcription: { finalCount: 1, finalText: "回答です。" },
      interview: {
        currentFieldBeforeId: "field-before",
        currentFieldAfterId: "field-after",
        expectedFieldAfterId: "field-after",
      },
    });
  } finally {
    fs.rmSync(outputDir, { recursive: true, force: true });
  }
});
