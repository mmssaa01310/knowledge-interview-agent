import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import {
  VOICE_SCENARIOS,
  chooseNextAction,
  deterministicSeed,
} from "./scenarios.mjs";

test("all deterministic voice scenarios are available without external agents", async () => {
  const config = readVoiceTestConfig();
  const guard = new VoiceCostGuard(config);
  const names = ["NORMAL", "HESITATION", "SELF_CORRECTION", "MULTI_ANSWER", "AMBIGUOUS"];
  for (const name of names) {
    const action = await chooseNextAction({
      scenario: VOICE_SCENARIOS[name],
      turn: 0,
      state: { stateVersion: 0 },
      config: { ...config, agentMode: "deterministic" },
      guard,
    });
    expect(typeof action).toBe("string");
  }
  expect(deterministicSeed(38291)).toBe(38291);
  expect(guard.snapshot().jevCalls).toBe(0);
});
