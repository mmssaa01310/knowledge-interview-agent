import type { GPTLiveEvent } from "../webrtc/gptLivePeerConnection";

export function isInputTranscriptDelta(type: string): boolean {
  return type === "session.input_transcript.delta"
    || type === "input_transcript.delta"
    || type === "conversation.item.input_audio_transcription.delta";
}

export function isOutputTranscriptDelta(type: string): boolean {
  return type === "session.output_transcript.delta"
    || type === "output_transcript.delta"
    || type === "response.output_transcript.delta"
    || type === "response.output_audio_transcript.delta";
}

export function isUserSpeechEndEvent(type: string): boolean {
  return [
    "session.input_transcript.done",
    "input_transcript.done",
    "session.input_transcript.completed",
    "conversation.item.input_audio_transcription.completed",
    "session.input_audio_buffer.speech_stopped",
    "input_audio_buffer.speech_stopped",
  ].includes(type);
}

export function isUserSpeechStartEvent(type: string): boolean {
  return [
    "session.input_audio_buffer.speech_started",
    "input_audio_buffer.speech_started",
    "session.input_speech_started",
    "input_speech_started",
  ].includes(type);
}

export function isResponseStartEvent(type: string | undefined): boolean {
  return type === "response.created"
    || type === "response.output_audio.delta"
    || type === "session.output_audio.delta"
    || type === "response.output_transcript.delta"
    || type === "response.output_audio_transcript.delta"
    || type === "session.output_transcript.delta";
}

export function isResponseAudioEvent(type: string | undefined): boolean {
  return type === "response.output_audio.delta"
    || type === "session.output_audio.delta"
    || type === "response.audio.delta";
}

export function isResponseDoneEvent(type: string | undefined): boolean {
  return type === "response.done"
    || type === "response.output_audio.done"
    || type === "session.output_audio.done"
    || type === "response.audio.done";
}

export function isResponseFunctionCallEvent(type: string | undefined): boolean {
  return type === "response.output_item.done"
    || type === "response.function_call_arguments.done";
}

export function isDelegatedResponseTerminalEvent(type: string | undefined): boolean {
  return type === "response.completed"
    || type === "response.done"
    || type === "response.failed"
    || type === "response.incomplete"
    || type === "response.cancelled";
}

export function readNestedResponseEvent(event: GPTLiveEvent): Record<string, unknown> | undefined {
  const nested = event.event;
  return nested && typeof nested === "object"
    ? nested as Record<string, unknown>
    : undefined;
}

export function readResponseFunctionCall(event: GPTLiveEvent): Record<string, unknown> | undefined {
  const candidates = [event, readNestedResponseEvent(event)].filter(
    (candidate): candidate is Record<string, unknown> => Boolean(candidate),
  );
  for (const candidate of candidates) {
    const item = candidate.item;
    if (!item || typeof item !== "object") {
      continue;
    }
    const itemRecord = item as Record<string, unknown>;
    if (itemRecord.type === "function_call") {
      return itemRecord;
    }
  }
  for (const candidate of candidates) {
    if (candidate.type !== "response.function_call_arguments.done") {
      continue;
    }
    if (typeof candidate.name !== "string" || typeof candidate.call_id !== "string") {
      continue;
    }
    return {
      type: "function_call",
      name: candidate.name,
      call_id: candidate.call_id,
      arguments: typeof candidate.arguments === "string" ? candidate.arguments : "{}",
    };
  }
  return undefined;
}

export function readDelegationId(event: GPTLiveEvent): string | undefined {
  const delegation = event.delegation;
  if (delegation && typeof delegation === "object" && "id" in delegation) {
    const id = (delegation as Record<string, unknown>).id;
    if (typeof id === "string" && id.trim()) {
      return id;
    }
  }
  const delegationId = event.delegation_id;
  return typeof delegationId === "string" && delegationId.trim()
    ? delegationId
    : undefined;
}

export function resolveDelegationId(
  event: GPTLiveEvent,
  delegatedResponseIds: Map<string, string>,
): string | undefined {
  const directId = readDelegationId(event);
  if (directId) {
    return directId;
  }
  const responseId = readResponseId(event);
  if (!responseId) {
    return undefined;
  }
  for (const [delegationId, mappedResponseId] of delegatedResponseIds) {
    if (mappedResponseId === responseId) {
      return delegationId;
    }
  }
  return undefined;
}

export function readResponseId(event: GPTLiveEvent): string | undefined {
  const nested = readNestedResponseEvent(event);
  const response = nested?.response;
  if (response && typeof response === "object" && "id" in response) {
    const id = (response as Record<string, unknown>).id;
    if (typeof id === "string" && id.trim()) {
      return id;
    }
  }
  const delegation = event.delegation;
  const delegationRecord = delegation && typeof delegation === "object"
    ? delegation as Record<string, unknown>
    : undefined;
  for (const candidate of [event, nested, delegationRecord]) {
    if (!candidate) {
      continue;
    }
    for (const key of ["response_id", "responseId"]) {
      const value = candidate[key];
      if (typeof value === "string" && value.trim()) {
        return value;
      }
    }
  }
  return undefined;
}

export function readClientEventId(event: GPTLiveEvent): string | undefined {
  const clientEventId = event.client_event_id;
  if (typeof clientEventId === "string" && clientEventId.trim()) {
    return clientEventId;
  }
  return typeof event.event_id === "string" && event.event_id.trim()
    ? event.event_id
    : undefined;
}

export function isErrorEvent(type: string): boolean {
  return type === "error" || type.endsWith(".error");
}

export function readErrorField(error: unknown, field: "code" | "message"): string | undefined {
  if (!error || typeof error !== "object" || !(field in error)) {
    return undefined;
  }
  const value = (error as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
}

export function transcriptTiming(event: GPTLiveEvent): { start_ms?: number; end_ms?: number } {
  return {
    ...(typeof event.start_ms === "number" ? { start_ms: event.start_ms } : {}),
    ...(typeof event.end_ms === "number" ? { end_ms: event.end_ms } : {}),
  };
}
