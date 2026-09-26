import fs from "node:fs";

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
      const recordLifecycle = (type, details = {}) => {
        trace.push({
          direction: "client",
          timestampMs: Date.now(),
          performanceMs: Math.round(performance.now()),
          raw: null,
          event: {
            type,
            label: channel.label ?? null,
            id: channel.id ?? null,
            readyState: channel.readyState ?? null,
            ...details,
          },
        });
      };
      channel.addEventListener("open", () => recordLifecycle("data_channel.open"));
      channel.addEventListener("close", () => recordLifecycle("data_channel.close"));
      channel.addEventListener("error", (event) => recordLifecycle("data_channel.error", {
        message: event?.error?.message ?? null,
      }));
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

/**
 * Record enough browser media information to prove whether the fixture reached
 * the WebRTC sender. This is test-only instrumentation; it does not alter the
 * application media path.
 */
export async function installVoiceAudioTrace(page) {
  await page.addInitScript(() => {
    const trace = { events: [], stats: [] };
    const connections = new Set();
    const observedConnections = new WeakSet();
    const record = (kind, details = {}) => {
      trace.events.push({
        kind,
        timestampMs: Date.now(),
        performanceMs: Math.round(performance.now()),
        details,
      });
    };
    const trackDetails = (track) => ({
      id: track?.id ?? null,
      kind: track?.kind ?? null,
      enabled: track?.enabled ?? null,
      muted: track?.muted ?? null,
      readyState: track?.readyState ?? null,
    });
    const observeConnection = (connection) => {
      if (!connection || observedConnections.has(connection)) return;
      observedConnections.add(connection);
      connections.add(connection);
      for (const eventName of [
        "connectionstatechange",
        "iceconnectionstatechange",
        "icegatheringstatechange",
        "signalingstatechange",
      ]) {
        connection.addEventListener(eventName, () => {
          record(eventName, {
            connectionState: connection.connectionState,
            iceConnectionState: connection.iceConnectionState,
            iceGatheringState: connection.iceGatheringState,
            signalingState: connection.signalingState,
          });
        });
      }
      connection.addEventListener("track", (event) => {
        record("remote_track_received", {
          track: trackDetails(event.track),
          streamIds: event.streams?.map((stream) => stream?.id ?? null) ?? [],
        });
      });
      record("peer_connection_observed", {
        connectionState: connection.connectionState,
        iceConnectionState: connection.iceConnectionState,
      });
    };
    window.__kikioriVoiceAudioTrace = trace;

    const mediaDevices = navigator.mediaDevices;
    const originalGetUserMedia = mediaDevices?.getUserMedia?.bind(mediaDevices);
    if (originalGetUserMedia) {
      mediaDevices.getUserMedia = async (constraints) => {
        record("get_user_media_requested", { constraints });
        try {
          const stream = await originalGetUserMedia(constraints);
          record("get_user_media_ready", {
            id: stream.id,
            tracks: stream.getTracks().map(trackDetails),
          });
          for (const track of stream.getTracks()) {
            track.addEventListener("ended", () => record("track_ended", trackDetails(track)));
          }
          return stream;
        } catch (error) {
          record("get_user_media_failed", {
            name: error?.name ?? null,
            message: error?.message ?? String(error),
          });
          throw error;
        }
      };
    }

    const peerPrototype = window.RTCPeerConnection?.prototype;
    if (peerPrototype) {
      const originalAddTrack = peerPrototype.addTrack;
      peerPrototype.addTrack = function (...args) {
        observeConnection(this);
        record("track_added", {
          track: trackDetails(args[0]),
          streamIds: args.slice(1).map((stream) => stream?.id ?? null),
        });
        return originalAddTrack.apply(this, args);
      };
      for (const methodName of ["createOffer", "setLocalDescription", "setRemoteDescription"]) {
        const original = peerPrototype[methodName];
        if (typeof original !== "function") continue;
        peerPrototype[methodName] = async function (...args) {
          observeConnection(this);
          record(methodName + "_started", {
            type: args[0]?.type ?? null,
            hasSdp: Boolean(args[0]?.sdp),
          });
          try {
            const result = await original.apply(this, args);
            record(methodName + "_completed", {
              connectionState: this.connectionState,
              iceConnectionState: this.iceConnectionState,
              hasLocalSdp: Boolean(this.localDescription?.sdp),
              hasRemoteSdp: Boolean(this.remoteDescription?.sdp),
            });
            return result;
          } catch (error) {
            record(methodName + "_failed", {
              message: error?.message ?? String(error),
            });
            throw error;
          }
        };
      }
    }

    window.setInterval(() => {
      for (const connection of connections) {
        if (!connection || connection.connectionState === "closed") continue;
        Promise.resolve(connection.getStats?.()).then((report) => {
          const outboundRtp = [];
          const inboundRtp = [];
          if (report?.forEach) {
            report.forEach((item) => {
              const kind = item.kind ?? item.mediaType;
              if (kind !== "audio") return;
              if (item?.type === "outbound-rtp") {
                outboundRtp.push({
                  id: item.id ?? null,
                  kind,
                  packetsSent: item.packetsSent ?? 0,
                  bytesSent: item.bytesSent ?? 0,
                  totalSamplesSent: item.totalSamplesSent ?? null,
                  timestamp: item.timestamp ?? null,
                });
              } else if (item?.type === "inbound-rtp") {
                inboundRtp.push({
                  id: item.id ?? null,
                  kind,
                  packetsReceived: item.packetsReceived ?? 0,
                  bytesReceived: item.bytesReceived ?? 0,
                  totalSamplesReceived: item.totalSamplesReceived ?? null,
                  audioLevel: typeof item.audioLevel === "number" ? item.audioLevel : null,
                  totalAudioEnergy: typeof item.totalAudioEnergy === "number"
                    ? item.totalAudioEnergy
                    : null,
                  totalSamplesDuration: typeof item.totalSamplesDuration === "number"
                    ? item.totalSamplesDuration
                    : null,
                  timestamp: item.timestamp ?? null,
                });
              }
            });
          }
          trace.stats.push({
            timestampMs: Date.now(),
            performanceMs: Math.round(performance.now()),
            connectionState: connection.connectionState,
            iceConnectionState: connection.iceConnectionState,
            tracks: connection.getSenders?.().map((sender) => trackDetails(sender.track)) ?? [],
            outboundRtp,
            inboundRtp,
          });
        }).catch((error) => record("get_stats_failed", {
          message: error?.message ?? String(error),
        }));
      }
    }, 500);
  });
}

/**
 * Install a test-only MediaStream microphone whose recorded WAV starts only
 * after the runner explicitly calls the controller. Chromium's
 * --use-file-for-fake-audio-capture starts playback when getUserMedia is
 * requested, which is before the Live session and the initial question are
 * ready. That loses the beginning of a real answer and can prevent server VAD
 * from producing a speech_started/speech_stopped pair.
 */
export async function installWavFixtureMicrophone(page, fixturePath) {
  const fixturePaths = Array.isArray(fixturePath) ? fixturePath : [fixturePath];
  const encodedWavs = fixturePaths.map((fixture) => fs.readFileSync(fixture).toString("base64"));
  await page.addInitScript(({ encodedWavs: initialEncodedWavs }) => {
    const state = {
      mode: "pending",
      ready: false,
      started: false,
      ended: false,
      requestedAt: null,
      startedAt: null,
      endedAt: null,
      error: null,
      start: null,
      replay: null,
      playCount: 0,
      currentFixtureIndex: 0,
    };
    window.__kikioriVoiceFixtureMicrophone = state;
    const recordFixtureEvent = (kind, details = {}) => {
      const trace = window.__kikioriVoiceAudioTrace;
      if (!trace?.events) return;
      trace.events.push({
        kind,
        timestampMs: Date.now(),
        performanceMs: Math.round(performance.now()),
        details,
      });
    };

    const mediaDevices = navigator.mediaDevices;
    const originalGetUserMedia = mediaDevices?.getUserMedia?.bind(mediaDevices);
    if (!mediaDevices || !originalGetUserMedia) {
      state.mode = "unavailable";
      recordFixtureEvent("wav_fixture_unavailable");
      return;
    }

    const decodeBase64 = (value) => {
      const binary = atob(value);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes.buffer;
    };

    mediaDevices.getUserMedia = async (constraints) => {
      if (!constraints?.audio) return originalGetUserMedia(constraints);
      state.requestedAt = Date.now();
      recordFixtureEvent("wav_fixture_requested", { constraints });
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        state.mode = "browser_fallback";
        recordFixtureEvent("wav_fixture_fallback", { reason: "audio_context_unavailable" });
        return originalGetUserMedia(constraints);
      }

      try {
        const audioContext = new AudioContextClass();
        if (typeof audioContext.createMediaStreamDestination !== "function") {
          state.mode = "browser_fallback";
          recordFixtureEvent("wav_fixture_fallback", { reason: "media_stream_destination_unavailable" });
          await audioContext.close().catch(() => undefined);
          return originalGetUserMedia(constraints);
        }
        const decoded = await Promise.all(initialEncodedWavs.map((encodedWav) => (
          audioContext.decodeAudioData(decodeBase64(encodedWav))
        )));
        const destination = audioContext.createMediaStreamDestination();
        let source = null;
        const createSource = () => {
          source = audioContext.createBufferSource();
          source.buffer = decoded[state.currentFixtureIndex] ?? decoded[0];
          source.connect(destination);
          source.addEventListener("ended", () => {
            state.ended = true;
            state.endedAt = Date.now();
            recordFixtureEvent("wav_fixture_ended", {
              fixtureDurationSeconds: source.buffer?.duration ?? null,
              playCount: state.playCount,
              fixtureIndex: state.currentFixtureIndex,
            });
          });
          return source;
        };
        createSource();
        state.mode = "controlled_wav";
        state.ready = true;
        recordFixtureEvent("wav_fixture_ready", {
          durationSeconds: decoded[0]?.duration ?? null,
          sampleRate: decoded[0]?.sampleRate ?? null,
          channelCount: decoded[0]?.numberOfChannels ?? null,
        });
        state.replay = async (fixtureIndex) => {
          if (state.started && !state.ended) {
            await new Promise((resolve) => {
              const deadline = Date.now() + 30_000;
              const waitForEnd = () => {
                if (state.ended || Date.now() >= deadline) {
                  resolve();
                  return;
                }
                window.setTimeout(waitForEnd, 50);
              };
              waitForEnd();
            });
          }
          await audioContext.resume();
          if (Number.isInteger(fixtureIndex)
            && fixtureIndex >= 0
            && fixtureIndex < decoded.length) {
            state.currentFixtureIndex = fixtureIndex;
          }
          createSource().start(0);
          state.started = true;
          state.ended = false;
          state.startedAt = Date.now();
          state.playCount += 1;
          recordFixtureEvent("wav_fixture_started", {
            durationSeconds: source.buffer?.duration ?? null,
            sampleRate: source.buffer?.sampleRate ?? null,
            channelCount: source.buffer?.numberOfChannels ?? null,
            fixtureIndex: state.currentFixtureIndex,
            playCount: state.playCount,
          });
          return true;
        };
        state.start = state.replay;
        return destination.stream;
      } catch (error) {
        state.mode = "decode_failed";
        state.error = error?.message ?? String(error);
        recordFixtureEvent("wav_fixture_decode_failed", { message: state.error });
        return originalGetUserMedia(constraints);
      }
    };
  }, { encodedWavs });
}

export async function startWavFixtureMicrophone(page) {
  return page.evaluate(async () => {
    const controller = window.__kikioriVoiceFixtureMicrophone;
    if (!controller) return { started: false, mode: "missing" };
    if (typeof controller.start === "function") {
      await controller.start();
    }
    return {
      started: Boolean(controller.started),
      mode: controller.mode,
      error: controller.error,
    };
  });
}

export async function replayWavFixtureMicrophone(page, fixtureIndex) {
  return page.evaluate(async (requestedFixtureIndex) => {
    const controller = window.__kikioriVoiceFixtureMicrophone;
    if (!controller) return { started: false, mode: "missing" };
    if (typeof controller.replay === "function") {
      await controller.replay(requestedFixtureIndex);
    } else if (typeof controller.start === "function") {
      await controller.start();
    }
    return {
      started: Boolean(controller.started),
      mode: controller.mode,
      playCount: controller.playCount ?? null,
      error: controller.error,
    };
  }, fixtureIndex);
}

export async function readVoiceAudioTrace(page) {
  return page.evaluate(() => window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] });
}

export function dataChannelEventPayload(entry) {
  const event = entry?.event;
  if (!event || typeof event !== "object") return {};
  if (event.type === "response.event" && event.event && typeof event.event === "object") {
    return event.event;
  }
  return event;
}

export function dataChannelEventType(entry) {
  const payload = dataChannelEventPayload(entry);
  return typeof payload.type === "string" ? payload.type : null;
}

export function realRuntimeTimeline({
  consoleEntries = [],
  dataChannelEntries = [],
  audioTrace = null,
  networkEntries = [],
} = {}) {
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
    details: dataChannelEventPayload(entry) || { raw: entry.raw },
    source: entry.direction === "client" ? "data_channel_client" : "data_channel_server",
  }));
  const audioTimeline = (audioTrace?.events ?? []).map((entry) => ({
    event: entry.kind || "browser_audio_event",
    timestampMs: entry.timestampMs ?? null,
    performanceMs: entry.performanceMs ?? null,
    details: entry.details ?? {},
    source: "browser_audio_trace",
  }));
  const networkTimeline = (networkEntries ?? [])
    .filter((entry) => (
      entry.direction === "request"
      || entry.direction === "response"
      || entry.direction === "websocket_open"
      || entry.direction === "websocket_close"
      || entry.direction === "websocket_error"
      || entry.direction === "websocket_frame_sent"
      || entry.direction === "websocket_frame_received"
    ))
    .map((entry) => ({
      event: /\/api\/live\/state-updates/.test(entry.url ?? "")
        ? (entry.direction === "request" ? "state_update_request" : "state_update_response")
        : /\/api\/live\/sessions/.test(entry.url ?? "")
          ? (entry.direction === "request" ? "live_session_request" : "live_session_response")
        : entry.frameEventType
          || entry.direction
          || "network_event",
      timestampMs: entry.timestampMs ?? null,
      performanceMs: null,
      details: {
        url: entry.url ?? null,
        method: entry.method ?? null,
        status: entry.status ?? null,
        responseBody: entry.responseBody ?? null,
        frameSize: entry.frameSize ?? null,
        frameType: entry.frameType ?? null,
        postData: entry.postData ?? null,
      },
      source: "network",
    }));
  return [...consoleTimeline, ...dataTimeline, ...audioTimeline, ...networkTimeline]
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
