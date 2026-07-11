import { API_BASE_URL } from "../../../lib/api";
import type { ChatMessage } from "../../../types/app";

type UseInterviewStreamArgs = {
  onDelta: (message: ChatMessage) => void;
  onProposalCreated: () => void;
};

export function useInterviewStream({ onDelta, onProposalCreated }: UseInterviewStreamArgs) {
  function start(recordId: string) {
    const source = new EventSource(`${API_BASE_URL}/api/records/${recordId}/stream`);
    source.addEventListener("delta", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as { text: string };
      onDelta({ role: "ai", text: data.text });
    });
    source.addEventListener("proposal_created", onProposalCreated);
    source.addEventListener("stream_end", () => source.close());
    source.onerror = () => source.close();
    return source;
  }

  return { start };
}
