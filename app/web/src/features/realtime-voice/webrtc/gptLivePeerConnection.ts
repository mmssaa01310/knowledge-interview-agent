import { createGPTLiveSession } from "../api/realtimeVoiceClient";
import type { VoiceConnectionStats } from "../types";
import { logVoiceStartupEvent } from "../utils/voiceTelemetry";

export type GPTLiveEvent = {
  type?: string;
  event_id?: string;
  delta?: string;
  session?: {
    id?: string;
  };
  [key: string]: unknown;
};

type GPTLivePeerConnectionOptions = {
  remoteAudioElement: HTMLAudioElement | null;
  recordId?: string;
  onEvent: (event: GPTLiveEvent) => void;
  onConnectionStateChange: (state: string) => void;
  onStatsChange: (stats: VoiceConnectionStats) => void;
  signal?: AbortSignal;
  startupStartedAt?: number;
};

export type GPTLivePeerConnectionHandle = {
  sessionId: string;
  sendEvent: (event: Record<string, unknown>) => boolean;
  stop: () => void;
};

const configuredIceGatheringTimeoutMs = Number.parseInt(
  import.meta.env.VITE_VOICE_ICE_GATHERING_TIMEOUT_MS ?? "1000",
  10,
);
const ICE_GATHERING_TIMEOUT_MS = Number.isFinite(configuredIceGatheringTimeoutMs)
  && configuredIceGatheringTimeoutMs > 0
  ? configuredIceGatheringTimeoutMs
  : 1000;
const SESSION_STARTED_TIMEOUT_MS = 10000;

export async function createGPTLivePeerConnection(
  options: GPTLivePeerConnectionOptions,
): Promise<GPTLivePeerConnectionHandle> {
  const peerConnection = new RTCPeerConnection();
  const markStartup = (event: string, voiceSessionId?: string, details?: Record<string, unknown>) => {
    if (options.startupStartedAt === undefined) {
      return;
    }
    logVoiceStartupEvent({
      event,
      provider: "gpt_live",
      voiceSessionId,
      startStartedAt: options.startupStartedAt,
      details,
    });
  };
  let dataChannel: RTCDataChannel | null = null;
  let microphoneStream: MediaStream | null = null;
  let remoteStream: MediaStream | null = null;
  let stopped = false;
  let sessionStartedSettled = false;
  let resolveSessionStarted: (() => void) | null = null;
  let rejectSessionStarted: ((reason?: unknown) => void) | null = null;
  let firstResponseEventLogged = false;
  let remoteAudioTrackReceivedAt: number | null = null;
  let startupSessionId: string | undefined;
  const sessionStarted = new Promise<void>((resolve, reject) => {
    resolveSessionStarted = () => resolve();
    rejectSessionStarted = reject;
  });
  // The connection can be aborted before the SDP answer is applied, so the
  // promise may reject before waitForSessionStarted() attaches its handler.
  void sessionStarted.catch(() => undefined);

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (!sessionStartedSettled) {
      sessionStartedSettled = true;
      rejectSessionStarted?.(createAbortError());
    }
    dataChannel?.close();
    microphoneStream?.getTracks().forEach((track) => track.stop());
    if (options.remoteAudioElement) {
      options.remoteAudioElement.pause();
      options.remoteAudioElement.onplaying = null;
      options.remoteAudioElement.srcObject = null;
    }
    peerConnection.close();
  };

  const handleDataChannelEvent = (event: GPTLiveEvent) => {
    if (event.type === "session.started" && !sessionStartedSettled) {
      sessionStartedSettled = true;
      resolveSessionStarted?.();
      markStartup("live_session_started", event.session?.id);
    } else if (isErrorEvent(event.type) && !sessionStartedSettled) {
      sessionStartedSettled = true;
      rejectSessionStarted?.(new Error("gpt_live_session_error_before_started"));
    } else if (isTerminalEvent(event.type) && !sessionStartedSettled) {
      sessionStartedSettled = true;
      rejectSessionStarted?.(new Error(
        event.type === "transport.closed"
          ? "gpt_live_transport_closed_before_started"
          : "gpt_live_session_closed_before_started",
      ));
    }
    if (!stopped) {
      if (!firstResponseEventLogged && event.type === "session.output_transcript.delta") {
        firstResponseEventLogged = true;
        markStartup("first_response_event_received", event.session?.id, {
          event_type: event.type,
        });
      }
      options.onEvent(event);
    }
    if (!stopped && (
      isErrorEvent(event.type)
      || isTerminalEvent(event.type)
    )) {
      stop();
    }
  };

  try {
    options.signal?.addEventListener("abort", stop, { once: true });
    throwIfAborted(options.signal);

    // 1-2. Register the remote audio track before requesting microphone access.
    peerConnection.addEventListener("track", (event) => {
      if (stopped) {
        return;
      }
      remoteStream = event.streams[0] ?? new MediaStream([event.track]);
      if (options.remoteAudioElement) {
        options.remoteAudioElement.srcObject = remoteStream;
      }
      remoteAudioTrackReceivedAt = performance.now();
      markStartup("remote_audio_track_received", startupSessionId, {
        track_id: event.track.id,
      });
      if (options.remoteAudioElement) {
        options.remoteAudioElement.onplaying = () => {
          const receivedAt = remoteAudioTrackReceivedAt;
          markStartup("audio_playing_started", startupSessionId, {
            remote_track_received_to_playing_ms: receivedAt === null
              ? undefined
              : Math.max(0, Math.round(performance.now() - receivedAt)),
          });
        };
      }
      options.onStatsChange({
        microphoneTrackLive: microphoneStream?.getAudioTracks().some((track) => track.readyState === "live") ?? false,
        remoteAudioTrackReceived: true,
      });
    });

    // 3. Browser microphone capture is the WebRTC media input.
    markStartup("get_user_media_started");
    const microphonePromise = navigator.mediaDevices.getUserMedia({ audio: true });
    const capturedMicrophoneStream = await waitForMediaStream(microphonePromise, options.signal);
    microphoneStream = capturedMicrophoneStream;
    markStartup("get_user_media_ready");
    if (stopped || options.signal?.aborted) {
      capturedMicrophoneStream.getTracks().forEach((track) => track.stop());
      throw createAbortError();
    }
    options.onStatsChange({
      microphoneTrackLive: capturedMicrophoneStream.getAudioTracks().some((track) => track.readyState === "live"),
      remoteAudioTrackReceived: false,
    });

    // 4. Add the microphone track to the peer connection.
    capturedMicrophoneStream.getAudioTracks().forEach((track) => {
      peerConnection.addTrack(track, capturedMicrophoneStream);
    });
    markStartup("microphone_track_added");

    // 5-6. The Live DataChannel carries JSON events only, never audio bytes.
    dataChannel = peerConnection.createDataChannel("oai-events");
    markStartup("data_channel_created");
    dataChannel.addEventListener("message", (messageEvent) => {
      const event = parseDataChannelEvent(messageEvent.data);
      if (event) {
        handleDataChannelEvent(event);
      }
    });
    dataChannel.addEventListener("open", () => {
      console.info("gpt_live_data_channel_open", { label: dataChannel?.label });
      markStartup("data_channel_open", startupSessionId);
    });
    dataChannel.addEventListener("close", () => {
      console.info("gpt_live_data_channel_closed");
      if (!stopped) {
        handleDataChannelEvent({ type: "transport.closed" });
      }
    });
    dataChannel.addEventListener("error", () => {
      console.warn("gpt_live_data_channel_error");
      if (!stopped) {
        handleDataChannelEvent({
          type: "error",
          error: { code: "data_channel_error" },
        });
      }
    });

    peerConnection.addEventListener("connectionstatechange", () => {
      if (!stopped) {
        options.onConnectionStateChange(peerConnection.connectionState);
      }
    });

    // 7-8. Create and set the browser's SDP offer.
    throwIfAborted(options.signal);
    const offer = await peerConnection.createOffer();
    throwIfAborted(options.signal);
    await peerConnection.setLocalDescription(offer);

    // 9. Wait for the fully gathered offer SDP.
    await waitForIceGatheringComplete(peerConnection, ICE_GATHERING_TIMEOUT_MS, options.signal);
    markStartup("browser_offer_ready");
    const offerSdp = peerConnection.localDescription?.sdp;
    if (!offerSdp) {
      throw new Error("webrtc_offer_sdp_missing");
    }

    // 10. FastAPI creates the Live session; the API key never reaches this code.
    markStartup("backend_session_request_started");
    const liveSession = await createGPTLiveSession(offerSdp, options.recordId, options.signal);
    startupSessionId = liveSession.session.id;
    markStartup("backend_session_ready", liveSession.session.id);

    // 11. Apply the SDP answer returned by FastAPI.
    await peerConnection.setRemoteDescription({
      type: "answer",
      sdp: liveSession.transport.sdp,
    });
    markStartup("remote_description_set", liveSession.session.id);

    // Live sessions are already started by the HTTP create call. The
    // DataChannel event is still the application-level readiness signal.
    await waitForSessionStarted(sessionStarted, options.signal, SESSION_STARTED_TIMEOUT_MS);

    if (!dataChannel || !microphoneStream) {
      throw new Error("gpt_live_connection_resources_missing");
    }

    // 12. The caller receives a ready handle only after session.started.
    return {
      sessionId: liveSession.session.id,
      sendEvent: (event: Record<string, unknown>) => {
        if (stopped || !dataChannel || dataChannel.readyState !== "open") {
          return false;
        }
        dataChannel.send(JSON.stringify(event));
        return true;
      },
      stop,
    };
  } catch (error) {
    stop();
    throw error;
  } finally {
    options.signal?.removeEventListener("abort", stop);
  }
}

function parseDataChannelEvent(data: unknown): GPTLiveEvent | null {
  if (typeof data !== "string") {
    console.warn("gpt_live_non_text_data_channel_message");
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(data);
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    return parsed as GPTLiveEvent;
  } catch {
    console.warn("gpt_live_invalid_data_channel_event");
    return null;
  }
}

function waitForMediaStream(
  streamPromise: Promise<MediaStream>,
  signal?: AbortSignal,
): Promise<MediaStream> {
  if (!signal) {
    return streamPromise;
  }
  throwIfAborted(signal);
  return new Promise<MediaStream>((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    function onAbort() {
      if (settled) return;
      settled = true;
      cleanup();
      reject(createAbortError());
    }
    signal.addEventListener("abort", onAbort, { once: true });
    streamPromise.then(
      (stream) => {
        if (settled || signal.aborted) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        settled = true;
        cleanup();
        resolve(stream);
      },
      (error: unknown) => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      },
    );
  });
}

async function waitForIceGatheringComplete(
  peerConnection: RTCPeerConnection,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal);
  if (peerConnection.iceGatheringState === "complete") {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let timeoutId: number | undefined;
    function finish(error?: Error) {
      if (settled) return;
      settled = true;
      peerConnection.removeEventListener("icegatheringstatechange", onStateChange);
      peerConnection.removeEventListener("connectionstatechange", onConnectionStateChange);
      signal?.removeEventListener("abort", onAbort);
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    }
    function onStateChange() {
      if (peerConnection.iceGatheringState === "complete") {
        finish();
      }
    }
    function onConnectionStateChange() {
      if (["failed", "closed"].includes(peerConnection.connectionState)) {
        finish(new Error("webrtc_ice_gathering_failed"));
      }
    }
    function onAbort() {
      finish(createAbortError());
    }
    timeoutId = window.setTimeout(
      () => finish(new Error("webrtc_ice_gathering_timeout")),
      timeoutMs,
    );
    peerConnection.addEventListener("icegatheringstatechange", onStateChange);
    peerConnection.addEventListener("connectionstatechange", onConnectionStateChange);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function waitForSessionStarted(
  sessionStarted: Promise<void>,
  signal: AbortSignal | undefined,
  timeoutMs: number,
): Promise<void> {
  throwIfAborted(signal);
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let timeoutId: number | undefined;
    function finish(error?: Error) {
      if (settled) return;
      settled = true;
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }
      signal?.removeEventListener("abort", onAbort);
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    }
    function onAbort() {
      finish(createAbortError());
    }
    timeoutId = window.setTimeout(
      () => finish(new Error("gpt_live_session_started_timeout")),
      timeoutMs,
    );
    signal?.addEventListener("abort", onAbort, { once: true });
    sessionStarted.then(() => finish(), (error: unknown) => {
      finish(error instanceof Error ? error : new Error("gpt_live_session_failed"));
    });
  });
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw createAbortError();
  }
}

function createAbortError(): DOMException {
  return new DOMException("gpt_live_connection_aborted", "AbortError");
}

function isErrorEvent(type: string | undefined): boolean {
  return type === "error" || type?.endsWith(".error") === true;
}

function isTerminalEvent(type: string | undefined): boolean {
  return type === "session.closed" || type === "transport.closed";
}
