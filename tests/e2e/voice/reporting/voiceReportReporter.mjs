import path from "node:path";

import { generateVoiceReportForRun } from "./voiceReportGenerator.mjs";

/**
 * Playwright reporter boundary for the static voice-run report.
 * Report failures are deliberately non-fatal: the test result is already
 * owned by Playwright, while the report is an observability artifact.
 */
export default class VoiceReportReporter {
  constructor(options = {}) {
    this.options = options;
  }

  async onEnd(result) {
    const outputDir = this.options.outputDir;
    if (!outputDir) return;
    try {
      const reportPath = generateVoiceReportForRun({
        runDir: outputDir,
        runId: this.options.runId,
        testStatus: result?.status ?? null,
      });
      const displayPath = path.relative(process.cwd(), reportPath) || reportPath;
      console.log("\nVoice test report:\n" + displayPath);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn("Voice test report warning: " + message);
    }
  }
}
