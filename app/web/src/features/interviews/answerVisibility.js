export function shouldUseVoiceAnswerSummary(fieldState) {
  if (!fieldState) {
    return false;
  }
  return fieldState.answerState === "CONFIRMED" && Boolean(fieldState.answerSummary?.trim());
}

export function isRawVoiceUserMessageHiddenFromAnswer(message) {
  return Boolean(message?.voiceSessionId);
}

export function getInterviewAnswerValue(fieldState) {
  if (!fieldState) {
    return "";
  }
  return fieldState.answerState === "CONFIRMED" && fieldState.answerSummary
    ? fieldState.answerSummary
    : "";
}

export function getInterviewDisplayAnswer(fieldState, editedValue) {
  if (fieldState?.answerState !== "CONFIRMED") {
    return "";
  }
  return editedValue ?? getInterviewAnswerValue(fieldState);
}

export function getInterviewAnswerStatusLabel(fieldState) {
  switch (fieldState?.answerState) {
    case "CANDIDATE_PENDING":
      return "追加確認中";
    case "AWAITING_CONFIRMATION":
      return "確認中";
    case "CONFIRMED":
      return "回答済み";
    default:
      return "未回答";
  }
}
