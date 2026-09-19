import type { GPTLiveCaptureResponse, LiveTranscriptFragment } from "../api/realtimeVoiceClient";

/** A paced observer, not a silence detector or a conversation turn boundary. */
export function createLiveTranscriptCapture(options: {
  submit: (revision: number, fragments: LiveTranscriptFragment[]) => Promise<GPTLiveCaptureResponse>;
  onSaved: (result: GPTLiveCaptureResponse) => void;
  onError: () => void;
}) {
  let pending: LiveTranscriptFragment[] = [];
  let batch: LiveTranscriptFragment[] | null = null;
  let revision = 1;
  let inFlight = false;
  let stopped = false;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function schedule(delay = 2000) {
    if (timer !== undefined || inFlight || (!pending.length && !batch)) return;
    timer = setTimeout(() => { timer = undefined; void flush(); }, delay);
  }

  async function flush() {
    if (inFlight || (!pending.length && !batch)) return;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    batch ??= pending.splice(0, 128);
    inFlight = true;
    try {
      const result = await options.submit(revision, batch);
      batch = null;
      revision += 1;
      failures = 0;
      // UI/context delivery errors must never replay an already saved batch.
      try { options.onSaved(result); } catch { console.warn("gpt_live_capture_refresh_failed"); }
    } catch {
      failures += 1;
      options.onError();
    } finally {
      inFlight = false;
      schedule(failures ? Math.min(30000, 2000 * 2 ** Math.min(failures, 4)) : stopped ? 0 : 2000);
    }
  }

  return {
    append(fragment: LiveTranscriptFragment) {
      if (stopped || !fragment.text) return;
      pending.push(fragment);
      // Never reset this timer on new speech. Continuous speech is saved too.
      schedule();
    },
    flush,
    stop() {
      stopped = true;
      // In-flight work and its trailing fragments survive audio disconnection.
      void flush();
    },
  };
}
