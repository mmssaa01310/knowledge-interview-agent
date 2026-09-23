import path from "node:path";

import {
  EvidenceDrivenRepairLoop,
  loadEvidencePackage,
} from "./repairLoop.mjs";

const args = process.argv.slice(2);
const evidenceIndex = args.indexOf("--evidence");
const evidenceDir = evidenceIndex >= 0 ? args[evidenceIndex + 1] : null;
if (!evidenceDir) {
  throw new Error("Usage: pnpm voice-self-heal -- --evidence <Evidence Package directory>");
}

const historyIndex = args.indexOf("--history");
const historyPath = historyIndex >= 0
  ? args[historyIndex + 1]
  : path.join(path.dirname(path.resolve(evidenceDir)), "failure-history.json");

const evidence = loadEvidencePackage(evidenceDir);
const loop = new EvidenceDrivenRepairLoop({ historyPath });
const result = await loop.run(evidenceDir);
console.log(JSON.stringify({
  failureId: result.record.failureId,
  status: result.status,
  evidenceDir: evidence.evidenceDir,
  confirmedFail: evidence.confirmedFail,
  missingFiles: evidence.missingFiles,
  level2Failed: evidence.level2Failed,
  level3Failed: evidence.level3Failed,
  repairAttempts: result.record.repairAttempts,
  level3ReplayCount: result.record.level3ReplayCount,
  reason: result.record.reason ?? null,
}, null, 2));

