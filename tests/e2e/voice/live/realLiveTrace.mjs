/**
 * Capture the structured arguments passed to the GPT Live browser logger.
 * The production transport stays untouched; this is installed only in a
 * Playwright page before the application bundle is evaluated.
 */
export async function installRealLiveTrace(page) {
  await page.addInitScript(() => {
    const trace = [];
    const serialize = (value, depth = 0) => {
      if (value === null || value === undefined) return value ?? null;
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return value;
      }
      if (depth > 4) return "[truncated]";
      if (Array.isArray(value)) return value.slice(0, 50).map((item) => serialize(item, depth + 1));
      if (typeof value === "object") {
        return Object.fromEntries(Object.entries(value).slice(0, 80).map(([key, item]) => (
          [key, serialize(item, depth + 1)]
        )));
      }
      return String(value);
    };
    window.__kikioriVoiceLiveTrace = { entries: trace };
    for (const method of ["debug", "info", "log", "warn", "error"]) {
      const original = console[method];
      console[method] = (...args) => {
        trace.push({
          method,
          timestampMs: Date.now(),
          performanceMs: Math.round(performance.now()),
          args: args.map((value) => serialize(value)),
        });
        original.apply(console, args);
      };
    }
  });
}

export async function readRealLiveTrace(page) {
  return page.evaluate(() => window.__kikioriVoiceLiveTrace?.entries ?? []);
}

/**
 * Observe the application WebRTC data channel without replacing the runtime.
 * GPT-Live logs provider events to the console, while Transcribe + Polly sends
 * its runtime events only through this channel.  The observer is installed in
 * the page before the application creates its peer connection.
 */
export async function installVoiceDataChannelTrace(page) {
  await page.addInitScript(() => {
    const trace = [];
    const record = (direction, value) => {
      let parsed = null;
      if (typeof value === "string") {
        try {
          parsed = JSON.parse(value);
        } catch {
          parsed = null;
        }
      }
      trace.push({
        direction,
        timestampMs: Date.now(),
        performanceMs: Math.round(performance.now()),
        raw: typeof value === "string" ? value : null,
        event: parsed,
      });
    };
    window.__kikioriVoiceDataChannelTrace = { entries: trace };
    const originalCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;
    RTCPeerConnection.prototype.createDataChannel = function (...args) {
      const channel = originalCreateDataChannel.apply(this, args);
      const originalSend = channel.send.bind(channel);
      channel.send = (value) => {
        record("client", value);
        return originalSend(value);
      };
      channel.addEventListener("message", (event) => {
        record("server", event.data);
      });
      return channel;
    };
  });
}

export async function readVoiceDataChannelTrace(page) {
  return page.evaluate(() => window.__kikioriVoiceDataChannelTrace?.entries ?? []);
}

export function dataChannelEventType(entry) {
  return typeof entry?.event?.type === "string" ? entry.event.type : null;
}

export function realRuntimeTimeline({ consoleEntries = [], dataChannelEntries = [] } = {}) {
  const consoleTimeline = (consoleEntries ?? []).map((entry) => ({
    event: loggerName(entry) || liveEventType(entry) || "browser_log",
    timestampMs: entry.timestampMs ?? null,
    performanceMs: entry.performanceMs ?? null,
    details: loggerDetails(entry),
    source: "console",
  }));
  const dataTimeline = (dataChannelEntries ?? []).map((entry) => ({
    event: dataChannelEventType(entry) || "data_channel_message",
    timestampMs: entry.timestampMs ?? null,
    performanceMs: entry.performanceMs ?? null,
    details: entry.event ?? { raw: entry.raw },
    source: entry.direction === "client" ? "data_channel_client" : "data_channel_server",
  }));
  return [...consoleTimeline, ...dataTimeline]
    .filter((entry) => entry.timestampMs !== null)
    .sort((left, right) => left.timestampMs - right.timestampMs);
}

export async function readGPTLiveTranscriptLines(page) {
  return page.locator(".gpt-live-transcript").evaluateAll((elements) => elements.map((element) => ({
    role: element.classList.contains("user") ? "user" : "assistant",
    text: element.querySelector("p")?.textContent?.trim() ?? element.textContent?.trim() ?? "",
  })));
}

function loggerName(entry) {
  return typeof entry?.args?.[0] === "string" ? entry.args[0] : "";
}

function loggerDetails(entry) {
  return entry?.args?.[1] && typeof entry.args[1] === "object" ? entry.args[1] : {};
}

export function liveEventType(entry) {
  const name = loggerName(entry);
  if (name === "gpt_live_event") return loggerDetails(entry).type ?? null;
  return null;
}

function firstTimestamp(entries, predicate) {
  return entries.find(predicate) ?? null;
}

function timestampOf(entry) {
  return typeof entry?.timestampMs === "number" ? entry.timestampMs : null;
}

export function extractRealTurnTrace(entries) {
  const source = Array.isArray(entries) ? entries : [];
  const speechStart = firstTimestamp(source, (entry) => {
    const type = liveEventType(entry);
    return typeof type === "string" && /(?:^|\.)input_audio_buffer\.speech_started$/.test(type);
  });
  const speechEnd = firstTimestamp(source, (entry) => loggerName(entry) === "gpt_live_user_speech_end");
  const speechEndMs = timestampOf(speechEnd);
  const afterSpeechEnd = speechEndMs === null
    ? source
    : source.filter((entry) => timestampOf(entry) === null || timestampOf(entry) >= speechEndMs);
  const transcriptFinal = firstTimestamp(source, (entry) => {
    const type = liveEventType(entry);
    return typeof type === "string"
      && (
        type.endsWith("input_audio_transcription.completed")
        || type.endsWith("input_transcript.completed")
        || type.endsWith("input_transcript.done")
      )
      && (speechEndMs === null || timestampOf(entry) === null || timestampOf(entry) >= speechEndMs);
  });
  const turnCommit = firstTimestamp(afterSpeechEnd, (entry) => loggerName(entry) === "gpt_live_state_persisted");
  const assistantSpeechStarted = firstTimestamp(afterSpeechEnd, (entry) => {
    const type = liveEventType(entry);
    return type === "assistant_speech_started" || loggerName(entry) === "gpt_live_response_started";
  });
  const assistantSpeechEnded = firstTimestamp(afterSpeechEnd, (entry) => {
    const type = liveEventType(entry);
    return type === "assistant_speech_ended" || loggerName(entry) === "gpt_live_response_done";
  });
  const transcriptFinalMs = timestampOf(transcriptFinal);
  const assistantStartMs = timestampOf(assistantSpeechStarted);
  return {
    speechStart: speechStart ? timestampOf(speechStart) : null,
    speechEnd: speechEndMs,
    transcriptFinal: transcriptFinalMs,
    turnCommit: turnCommit ? timestampOf(turnCommit) : null,
    assistantSpeechStarted: assistantStartMs,
    assistantSpeechEnded: assistantSpeechEnded ? timestampOf(assistantSpeechEnded) : null,
    speechEndToTranscriptFinalMs: speechEndMs !== null && transcriptFinalMs !== null
      ? Math.max(0, transcriptFinalMs - speechEndMs)
      : null,
    transcriptFinalToAssistantStartMs: transcriptFinalMs !== null && assistantStartMs !== null
      ? Math.max(0, assistantStartMs - transcriptFinalMs)
      : null,
    speechEndToAssistantStartMs: speechEndMs !== null && assistantStartMs !== null
      ? Math.max(0, assistantStartMs - speechEndMs)
      : null,
  };
}

export function realTraceTimeline(entries) {
  return (entries ?? []).map((entry) => ({
    event: loggerName(entry) || liveEventType(entry) || "browser_log",
    timestampMs: entry.timestampMs ?? null,
    performanceMs: entry.performanceMs ?? null,
    details: loggerDetails(entry),
  }));
}
