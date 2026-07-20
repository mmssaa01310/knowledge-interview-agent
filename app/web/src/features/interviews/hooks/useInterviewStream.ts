import { useEffect, useRef } from "react";
import { API_BASE_URL } from "../../../lib/api";
import type { InterviewStreamMetadata } from "../../../types/app";

type UseInterviewStreamArgs = {
  onDelta: (chunk: string) => void;
  onStreamEnd: (metadata: InterviewStreamMetadata | null) => void;
  onProposalCreated: () => void;
  onError?: () => void;
};

type StreamEndPayload = {
  metadata?: InterviewStreamMetadata;
};

export function useInterviewStream({ onDelta, onStreamEnd, onProposalCreated, onError }: UseInterviewStreamArgs) {
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => () => sourceRef.current?.close(), []);

  function start(recordId: string) {
    sourceRef.current?.close();
    const source = new EventSource(`${API_BASE_URL}/api/records/${recordId}/stream`);
    sourceRef.current = source;
    source.addEventListener("delta", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as { text: string };
      onDelta(data.text);
    });
    source.addEventListener("proposal_created", onProposalCreated);
    source.addEventListener("stream_end", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as StreamEndPayload;
      onStreamEnd(payload.metadata ?? null);
      source.close();
      if (sourceRef.current === source) {
        sourceRef.current = null;
      }
    });
    source.onerror = () => {
      source.close();
      if (sourceRef.current === source) {
        sourceRef.current = null;
      }
      onError?.();
    };
    return source;
  }

  return { start };
}
