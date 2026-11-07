import type { Knowledge } from "@ai-interviewer/shared-types";

export const DEFAULT_INTERVIEW_MODEL_ID = "gpt-6-luna" as const;

export function isInterviewConfigurationComplete(knowledge: Knowledge | null) {
  const plan = knowledge?.interviewPlan;
  return Boolean(
    plan
      && ["fixed_form", "business_process", "system_requirement"].includes(plan.profile ?? "")
      && ["gpt-6.1-terra", "gpt-6-luna"].includes(plan.modelId ?? ""),
  );
}
