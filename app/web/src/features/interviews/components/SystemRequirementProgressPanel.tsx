import type { InterviewState } from "../../../types/app";
import { useI18n, type Translate } from "../../../i18n";
import { formatNumber } from "../../../lib/date";

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

function statusForEntry(entry: RequirementEntry | undefined, t: Translate) {
  if (entry?.status === "CONFIRMED") {
    return { label: t("interview.system.statusConfirmed"), className: "confirmed", symbol: "✓" };
  }
  if (entry?.status === "AWAITING_CONFIRMATION") {
    return { label: t("interview.system.statusChecking"), className: "active", symbol: "●" };
  }
  if (entry?.status === "CANDIDATE_PENDING") {
    return { label: t("interview.system.statusCandidate"), className: "candidate", symbol: "△" };
  }
  return { label: t("interview.system.statusPending"), className: "pending", symbol: "○" };
}

function processApplicabilityStatus(status: ProcessStatus, t: Translate) {
  if (status === "present") {
    return { label: t("interview.system.statusPresent"), className: "confirmed", symbol: "✓" };
  }
  if (status === "not_applicable") {
    return { label: t("interview.system.statusNotApplicable"), className: "not-applicable", symbol: "–" };
  }
  return { label: t("interview.system.statusPending"), className: "pending", symbol: "○" };
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

function RequirementValue({ value, t }: { value: string; t: Translate }) {
  if (!value) return null;
  if (value.length <= MAX_INLINE_VALUE_LENGTH) {
    return <p>{value}</p>;
  }

  return (
    <details className="system-progress-value-details">
      <summary>{value.slice(0, MAX_INLINE_VALUE_LENGTH).trimEnd()}… {t("common.fullText")}</summary>
      <p>{value}</p>
    </details>
  );
}

function RequirementProgressItem({
  entry,
  current,
  t,
}: {
  entry: RequirementEntry | undefined;
  current: boolean;
  t: Translate;
}) {
  if (!entry) return null;
  const status = statusForEntry(entry, t);
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
      <RequirementValue value={value} t={t} />
      {current ? <span className="system-progress-current">{t("interview.system.currentTarget")}</span> : null}
    </div>
  );
}

export function SystemRequirementProgressPanel({ interviewState }: SystemRequirementProgressPanelProps) {
  const { t, locale } = useI18n();
  if (!interviewState || interviewState.interviewProfile !== "system_requirement") {
    return null;
  }

  const requirementStates = interviewState.requirementStates ?? {};
  const requirementEntries = REQUIREMENT_IDS
    .map((id) => requirementStates[id])
    .filter((entry): entry is RequirementEntry => Boolean(entry));
  const processStatus: ProcessStatus = interviewState.applicabilityState?.process?.status ?? "unknown";
  const processStateStatus = processApplicabilityStatus(processStatus, t);
  const confirmedRequirementCount = requirementEntries.filter((entry) => entry.status === "CONFIRMED").length;
  const nextTarget = interviewState.nextQuestionTarget;
  const isProcessApplicabilityCurrent = nextTarget?.targetType === "applicability"
    && nextTarget.targetId === "process";

  return (
    <div className="system-requirement-progress" aria-label={t("interview.system.ariaLabel")}>
      <div className="system-progress-header">
        <div>
          <strong>{t("interview.system.title")}</strong>
          <p>{t("interview.system.description")}</p>
        </div>
        <span className="status-pill muted">{t("interview.system.confirmedCount", { confirmed: formatNumber(confirmedRequirementCount, locale), total: formatNumber(requirementEntries.length, locale) })}</span>
      </div>

      <section className="system-progress-section" aria-labelledby="system-requirement-items-title">
        <div className="system-progress-section-title">
          <strong id="system-requirement-items-title">{t("interview.system.requirements")}</strong>
          <span>{t("interview.system.required")}</span>
        </div>
        <div className="system-progress-list">
          {REQUIREMENT_IDS.map((id) => (
            <RequirementProgressItem
              key={id}
              entry={requirementStates[id]}
              current={isCurrentTarget(interviewState, "requirement", id)}
              t={t}
            />
          ))}
        </div>
      </section>

      <section className="system-progress-section" aria-labelledby="system-process-title">
        <div className="system-progress-section-title">
          <strong id="system-process-title">{t("interview.system.process")}</strong>
          <span>{t("interview.system.processDescription")}</span>
        </div>
        <div className={`system-progress-applicability ${processStateStatus.className}${isProcessApplicabilityCurrent ? " current" : ""}`}>
          <div className="system-progress-item-header">
            <div className="system-progress-item-title">
              <span className={`system-progress-symbol ${processStateStatus.className}`} aria-hidden="true">{processStateStatus.symbol}</span>
              <strong>{t("interview.system.processPresence")}</strong>
            </div>
            <span className={`status-pill ${processStateStatus.className}`}>{processStateStatus.label}</span>
          </div>
          {isProcessApplicabilityCurrent ? <span className="system-progress-current">{t("interview.system.currentTarget")}</span> : null}
        </div>

        {processStatus === "present" ? (
          <>
            <div className="system-progress-subsection-title">{t("interview.system.processContent")}</div>
            <div className="system-progress-list">
              {PROCESS_REQUIRED_IDS.map((id) => (
                <RequirementProgressItem
                  key={id}
                  entry={requirementStates[id]}
                  current={isCurrentTarget(interviewState, "process", id)}
                  t={t}
                />
              ))}
            </div>
            <details className="system-progress-optional" open={PROCESS_OPTIONAL_IDS.some((id) => isCurrentTarget(interviewState, "process", id))}>
              <summary>{t("interview.system.additionalCheck")}</summary>
              <div className="system-progress-list">
                {PROCESS_OPTIONAL_IDS.map((id) => (
                  <RequirementProgressItem
                    key={id}
                    entry={requirementStates[id]}
                    current={isCurrentTarget(interviewState, "process", id)}
                    t={t}
                  />
                ))}
              </div>
            </details>
          </>
        ) : null}
      </section>

      {nextTarget ? (
        <div className="system-progress-next">
          <span>{t("interview.system.nextTarget")}</span>
          <strong>{nextTarget.label}</strong>
        </div>
      ) : null}
    </div>
  );
}
