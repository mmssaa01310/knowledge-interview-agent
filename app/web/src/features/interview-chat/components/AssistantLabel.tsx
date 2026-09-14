export type AssistantLabelState = "default" | "thinking" | "error";

type AssistantLabelProps = {
  state: AssistantLabelState;
  label: string;
};

export function AssistantLabel({ state, label }: AssistantLabelProps) {
  return <span className={`assistant-label ${state}`}>{label}</span>;
}
