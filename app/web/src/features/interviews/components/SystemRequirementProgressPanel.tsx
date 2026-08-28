import type { InterviewState } from "../../../types/app";

type SystemRequirementProgressPanelProps = {
  interviewState: InterviewState | null;
};

type RequirementEntry = NonNullable<InterviewState["requirementStates"]>[string];
type ProcessStatus = "unknown" | "present" | "not_applicable";

const REQUIREMENT_IDS = [
  "requirement.purpose_problem",
  "requirement.users",
  "requirement.request",
  "requirement.expected_result",
  "requirement.constraints",
];

const PROCESS_REQUIRED_IDS = [
  "process.trigger",
  "process.actors",
  "process.main_flow",
  "process.end",
  "process.interaction",
];

const PROCESS_OPTIONAL_IDS = [
  "process.branch",
  "process.exception",
  "process.external_system",
  "process.error_handling",
  "process.handoff",
  "process.input_output",
];

const MAX_INLINE_VALUE_LENGTH = 96;

function statusForEntry(entry: RequirementEntry | undefined) {
  if (entry?.status === "CONFIRMED") {
    return { label: "確定", className: "confirmed", symbol: "✓" };
  }
  if (entry?.status === "AWAITING_CONFIRMATION") {
    return { label: "確認中", className: "active", symbol: "●" };
  }
  if (entry?.status === "CANDIDATE_PENDING") {
    return { label: "候補", className: "candidate", symbol: "△" };
  }
  return { label: "未確認", className: "pending", symbol: "○" };
}

function processApplicabilityStatus(status: ProcessStatus) {
  if (status === "present") {
    return { label: "あり", className: "confirmed", symbol: "✓" };
  }
  if (status === "not_applicable") {
    return { label: "対象外", className: "not-applicable", symbol: "–" };
  }
  return { label: "未確認", className: "pending", symbol: "○" };
}

function valueForEntry(entry: RequirementEntry | undefined) {
  if (!entry) return "";
  return entry.status === "CONFIRMED"
    ? entry.value?.trim() ?? ""
    : entry.candidateValue?.trim() ?? "";
}

function isCurrentTarget(
  interviewState: InterviewState,
  targetType: string,
  targetId: string,
) {
  const target = interviewState.nextQuestionTarget;
  return target?.targetType === targetType && target.targetId === targetId;
}

function RequirementValue({ value }: { value: string }) {
  if (!value) return null;
  if (value.length <= MAX_INLINE_VALUE_LENGTH) {
    return <p>{value}</p>;
  }

  return (
    <details className="system-progress-value-details">
      <summary>{value.slice(0, MAX_INLINE_VALUE_LENGTH).trimEnd()}… 全文を表示</summary>
      <p>{value}</p>
    </details>
  );
}

function RequirementProgressItem({
  entry,
  current,
}: {
  entry: RequirementEntry | undefined;
  current: boolean;
}) {
  if (!entry) return null;
  const status = statusForEntry(entry);
  const value = valueForEntry(entry);
  return (
    <div className={`system-progress-item ${status.className}${current ? " current" : ""}`}>
      <div className="system-progress-item-header">
        <div className="system-progress-item-title">
          <span className={`system-progress-symbol ${status.className}`} aria-hidden="true">{status.symbol}</span>
          <strong>{entry.label}</strong>
        </div>
        <span className={`status-pill ${status.className}`}>{status.label}</span>
      </div>
      <RequirementValue value={value} />
      {current ? <span className="system-progress-current">現在の確認対象</span> : null}
    </div>
  );
}

export function SystemRequirementProgressPanel({ interviewState }: SystemRequirementProgressPanelProps) {
  if (!interviewState || interviewState.interviewProfile !== "system_requirement") {
    return null;
  }

  const requirementStates = interviewState.requirementStates ?? {};
  const requirementEntries = REQUIREMENT_IDS
    .map((id) => requirementStates[id])
    .filter((entry): entry is RequirementEntry => Boolean(entry));
  const processStatus: ProcessStatus = interviewState.applicabilityState?.process?.status ?? "unknown";
  const processStateStatus = processApplicabilityStatus(processStatus);
  const confirmedRequirementCount = requirementEntries.filter((entry) => entry.status === "CONFIRMED").length;
  const nextTarget = interviewState.nextQuestionTarget;
  const isProcessApplicabilityCurrent = nextTarget?.targetType === "applicability"
    && nextTarget.targetId === "process";

  return (
    <div className="system-requirement-progress" aria-label="システム要件の確認状況">
      <div className="system-progress-header">
        <div>
          <strong>要件整理</strong>
          <p>会話から整理した内容です。候補は確認前です。</p>
        </div>
        <span className="status-pill muted">{confirmedRequirementCount}/{requirementEntries.length} 確定</span>
      </div>

      <section className="system-progress-section" aria-labelledby="system-requirement-items-title">
        <div className="system-progress-section-title">
          <strong id="system-requirement-items-title">システム要件</strong>
          <span>必須</span>
        </div>
        <div className="system-progress-list">
          {REQUIREMENT_IDS.map((id) => (
            <RequirementProgressItem
              key={id}
              entry={requirementStates[id]}
              current={isCurrentTarget(interviewState, "requirement", id)}
            />
          ))}
        </div>
      </section>

      <section className="system-progress-section" aria-labelledby="system-process-title">
        <div className="system-progress-section-title">
          <strong id="system-process-title">業務フロー</strong>
          <span>処理の有無と内容</span>
        </div>
        <div className={`system-progress-applicability ${processStateStatus.className}${isProcessApplicabilityCurrent ? " current" : ""}`}>
          <div className="system-progress-item-header">
            <div className="system-progress-item-title">
              <span className={`system-progress-symbol ${processStateStatus.className}`} aria-hidden="true">{processStateStatus.symbol}</span>
              <strong>業務フローの有無</strong>
            </div>
            <span className={`status-pill ${processStateStatus.className}`}>{processStateStatus.label}</span>
          </div>
          {isProcessApplicabilityCurrent ? <span className="system-progress-current">現在の確認対象</span> : null}
        </div>

        {processStatus === "present" ? (
          <>
            <div className="system-progress-subsection-title">処理内容</div>
            <div className="system-progress-list">
              {PROCESS_REQUIRED_IDS.map((id) => (
                <RequirementProgressItem
                  key={id}
                  entry={requirementStates[id]}
                  current={isCurrentTarget(interviewState, "process", id)}
                />
              ))}
            </div>
            <details className="system-progress-optional" open={PROCESS_OPTIONAL_IDS.some((id) => isCurrentTarget(interviewState, "process", id))}>
              <summary>追加確認（分岐・例外など）</summary>
              <div className="system-progress-list">
                {PROCESS_OPTIONAL_IDS.map((id) => (
                  <RequirementProgressItem
                    key={id}
                    entry={requirementStates[id]}
                    current={isCurrentTarget(interviewState, "process", id)}
                  />
                ))}
              </div>
            </details>
          </>
        ) : null}
      </section>

      {nextTarget ? (
        <div className="system-progress-next">
          <span>次に確認すること</span>
          <strong>{nextTarget.label}</strong>
        </div>
      ) : null}
    </div>
  );
}
