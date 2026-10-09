import type { GPTLiveCaptureResponse, GPTLiveCaptureReceipt, LiveTranscriptFragment } from "../api/realtimeVoiceClient";

/** A paced observer, not a silence detector or a conversation turn boundary. */
export function createLiveTranscriptCapture(options: {
  submit: (revision: number, fragments: LiveTranscriptFragment[]) => Promise<GPTLiveCaptureResponse | GPTLiveCaptureReceipt>;
  poll?: () => Promise<GPTLiveCaptureResponse>;
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
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let polling = false;
  let processing = false;
  const page = typeof window === "undefined" ? undefined : window;
  const documentRef = typeof document === "undefined" ? undefined : document;
  const hasUnsent = () => inFlight || pending.length > 0 || batch !== null;
  const onPageHide = () => { void flush(); };
  const onHidden = () => { if (documentRef?.visibilityState === "hidden") void flush(); };
  const onBeforeUnload = (event: BeforeUnloadEvent) => {
    if (!hasUnsent()) return;
    void flush();
    event.preventDefault();
    event.returnValue = "";
  };
  page?.addEventListener("pagehide", onPageHide);
  page?.addEventListener("beforeunload", onBeforeUnload);
  documentRef?.addEventListener("visibilitychange", onHidden);

  function releasePageListeners() {
    if (!stopped || hasUnsent()) return;
    page?.removeEventListener("pagehide", onPageHide);
    page?.removeEventListener("beforeunload", onBeforeUnload);
    documentRef?.removeEventListener("visibilitychange", onHidden);
  }

  function schedulePoll() {
    if (!options.poll || polling || pollTimer !== undefined) return;
    pollTimer = setTimeout(() => { pollTimer = undefined; void poll(); }, 2000);
  }

  async function poll() {
    if (!options.poll) return;
    polling = true;
    const polledRevision = revision;
    try {
      const result = await options.poll();
      // Keep observations visible during continuous speech, but a stale
      // response cannot complete a session or stop polling newer receipts.
      processing = Boolean(result.processing) || revision !== polledRevision || hasUnsent();
      options.onSaved({ ...result, processing });
    } catch {
      processing = true;
      options.onError();
    } finally {
      polling = false;
      if (!stopped || processing || inFlight || pending.length || batch) schedulePoll();
    }
  }

  function schedule(delay = 2000) {
    if (timer !== undefined || inFlight || (!pending.length && !batch)) return;
    timer = setTimeout(() => { timer = undefined; void flush(); }, delay);
  }

  async function flush() {
    if (inFlight || (!pending.length && !batch)) return;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (!batch) {
      batch = [];
      // Leave headroom under fetch keepalive's shared 64 KiB body limit.
      let bytes = 0;
      while (pending.length && batch.length < 128) {
        const size = new TextEncoder().encode(JSON.stringify(pending[0])).length;
        if (batch.length && bytes + size > 24000) break;
        bytes += size;
        batch.push(pending.shift()!);
      }
    }
    inFlight = true;
    try {
      const result = await options.submit(revision, batch);
      batch = null;
      revision += 1;
      failures = 0;
      // UI/context delivery errors must never replay an already saved batch.
      if (result.status === "accepted") {
        processing = true;
        schedulePoll();
      } else {
        try { options.onSaved(result); } catch { console.warn("gpt_live_capture_refresh_failed"); }
      }
    } catch {
      failures += 1;
      options.onError();
    } finally {
      inFlight = false;
      releasePageListeners();
      schedule(failures ? Math.min(30000, 2000 * 2 ** Math.min(failures, 4)) : stopped ? 0 : 2000);
    }
  }

  return {
    append(fragment: LiveTranscriptFragment) {
      if (stopped || !fragment.text) return;
      const characters = Array.from(fragment.text);
      for (let index = 0; index < characters.length; index += 4000) {
        pending.push({ ...fragment, text: characters.slice(index, index + 4000).join("") });
      }
      // Never reset this timer on new speech. Continuous speech is saved too.
      schedule();
    },
    flush,
    hasUnsent,
    isProcessing: () => processing || inFlight || pending.length > 0 || batch !== null,
    stop() {
      stopped = true;
      // In-flight work and its trailing fragments survive audio disconnection.
      void flush();
      releasePageListeners();
    },
  };
}
