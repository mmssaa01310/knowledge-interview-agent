import type { Knowledge } from "@ai-interviewer/shared-types";
import type { Translate } from "../../i18n";

type InterviewProfile = NonNullable<NonNullable<Knowledge["interviewPlan"]>["profile"]>;

export const DEFAULT_INTERVIEW_MODEL_ID = "global.openai.gpt-5.6-luna" as const;

const profileLabelKeys: Record<InterviewProfile, string> = {
  fixed_form: "interview.profile.fixed_form",
  business_process: "interview.profile.business_process",
  system_requirement: "interview.profile.system_requirement",
};

const modelLabelKeys: Record<string, string> = {
  "global.openai.gpt-5.6-terra": "interview.model.terra",
  "global.openai.gpt-5.6-luna": "interview.model.luna",
};

export function isInterviewConfigurationComplete(knowledge: Knowledge | null) {
  const plan = knowledge?.interviewPlan;
  return Boolean(
    plan
      && ["fixed_form", "business_process", "system_requirement"].includes(plan.profile ?? "")
      && ["global.openai.gpt-5.6-terra", "global.openai.gpt-5.6-luna"].includes(plan.modelId ?? ""),
  );
}

export function getInterviewProfileLabel(knowledge: Knowledge | null, translate?: Translate) {
  const profile = knowledge?.interviewPlan?.profile;
  if (!profile) return translate ? translate("interview.profile.notSet") : "";
  return translate ? translate(profileLabelKeys[profile]) : profile;
}

export function getInterviewModelLabel(knowledge: Knowledge | null, translate?: Translate) {
  const modelId = knowledge?.interviewPlan?.modelId;
  if (!modelId) return translate ? translate("common.notSet") : "";
  return translate ? translate(modelLabelKeys[modelId] ?? "common.unknown") : modelId;
}
