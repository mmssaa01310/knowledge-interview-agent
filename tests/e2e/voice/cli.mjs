import { spawn } from "node:child_process";

const args = process.argv.slice(2);
const replayIndex = args.indexOf("--replay");
if (replayIndex >= 0) {
  const seed = args[replayIndex + 1];
  if (!seed || !/^\d+$/.test(seed)) throw new Error("--replay requires a numeric seed");
  process.env.VOICE_TEST_SEED = seed;
}
const scenarioIndex = args.indexOf("--scenario");
if (scenarioIndex >= 0) {
  const scenario = args[scenarioIndex + 1];
  if (!scenario) throw new Error("--scenario requires a scenario name");
  process.env.VOICE_TEST_SCENARIO = scenario;
}

const omitted = new Set([
  "--replay",
  "--scenario",
  replayIndex >= 0 ? args[replayIndex + 1] : null,
  scenarioIndex >= 0 ? args[scenarioIndex + 1] : null,
]);
const forwarded = args.filter((arg) => !omitted.has(arg));
const child = spawn(
  "pnpm",
  ["exec", "playwright", "test", "tests/e2e/voice", "--project=chromium", ...forwarded],
  { cwd: process.cwd(), env: process.env, stdio: "inherit" },
);
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
