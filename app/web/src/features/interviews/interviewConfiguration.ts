import type { Knowledge } from "@ai-interviewer/shared-types";

type InterviewProfile = NonNullable<NonNullable<Knowledge["interviewPlan"]>["profile"]>;

const profileLabels: Record<InterviewProfile, string> = {
  fixed_form: "定型情報を聞き取る",
  business_process: "業務フローを整理する",
  system_requirement: "システム要件を整理する",
};

const modelLabels: Record<string, string> = {
  "global.openai.gpt-5.6-terra": "GPT-5.6 Terra",
  "global.openai.gpt-5.6-luna": "GPT-5.6 Luna",
};

export function isInterviewConfigurationComplete(knowledge: Knowledge | null) {
  const plan = knowledge?.interviewPlan;
  return Boolean(
    plan
      && ["fixed_form", "business_process", "system_requirement"].includes(plan.profile ?? "")
      && ["global.openai.gpt-5.6-terra", "global.openai.gpt-5.6-luna"].includes(plan.modelId ?? ""),
  );
}

export function getInterviewProfileLabel(knowledge: Knowledge | null) {
  const profile = knowledge?.interviewPlan?.profile;
  return profile ? profileLabels[profile] : "未設定";
}

export function getInterviewModelLabel(knowledge: Knowledge | null) {
  const modelId = knowledge?.interviewPlan?.modelId;
  return modelId ? modelLabels[modelId] ?? modelId : "未設定";
}
