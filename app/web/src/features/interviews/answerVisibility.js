export function shouldUseVoiceAnswerSummary(fieldState) {
  if (!fieldState) {
    return false;
  }
  return fieldState.answerState === "CONFIRMED" && Boolean(fieldState.recordAnswer?.trim());
}

export function isRawVoiceUserMessageHiddenFromAnswer(message) {
  return Boolean(message?.voiceSessionId);
}

export function getInterviewAnswerValue(fieldState) {
  if (!fieldState) {
    return "";
  }
  return fieldState.answerState === "CONFIRMED" && fieldState.recordAnswer
    ? fieldState.recordAnswer
    : "";
}

export function getInterviewDisplayAnswer(fieldState, editedValue) {
  if (fieldState?.answerState !== "CONFIRMED") {
    return "";
  }
  return editedValue ?? getInterviewAnswerValue(fieldState);
}

export function getInterviewAnswerStatusLabel(fieldState, translate) {
  const keyByState = {
    CANDIDATE_PENDING: "interview.answerStatus.candidate",
    AWAITING_CONFIRMATION: "interview.answerStatus.awaiting",
    CONFIRMED: "interview.answerStatus.confirmed",
  };
  const key = keyByState[fieldState?.answerState] ?? "interview.answerStatus.pending";
  if (translate) {
    return translate(key);
  }
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
