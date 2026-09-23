export async function installFakeLiveTransport(
  page,
  { sessionId = "e2e-live-session" } = {},
) {
  await page.addInitScript(({ sessionId: configuredSessionId }) => {
    const listeners = new Map();
    const sentEvents = [];
    const serverEvents = [];
    const channels = [];

    function on(target, type, listener) {
      const entries = listeners.get(target) ?? new Map();
      const callbacks = entries.get(type) ?? [];
      callbacks.push(listener);
      entries.set(type, callbacks);
      listeners.set(target, entries);
    }

    function emit(target, type, payload = {}) {
      const entries = listeners.get(target);
      for (const callback of entries?.get(type) ?? []) callback(payload);
    }

    function dispatchServer(event) {
      serverEvents.push({
        direction: "server",
        timestampMs: performance.now(),
        event,
      });
      for (const channel of channels) {
        emit(channel, "message", { data: JSON.stringify(event) });
      }
    }

    function emitAssistantResponse(
      text,
      responseId = "e2e-response-" + Date.now(),
      context = {},
    ) {
      const questionId = context.nextQuestion?.id ?? null;
      const questionText = context.nextQuestion?.text ?? null;
      const turnId = context.turnId ?? null;
      dispatchServer({
        type: "response.created",
        response: { id: responseId },
        turn_id: turnId,
        question_id: questionId,
        question_text: questionText,
      });
      dispatchServer({
        type: "assistant_speech_started",
        response_id: responseId,
        turn_id: turnId,
        question_id: questionId,
        question_text: questionText,
      });
      dispatchServer({
        type: "response.output_transcript.delta",
        response_id: responseId,
        delta: text,
        turn_id: turnId,
        question_id: questionId,
        question_text: questionText,
      });
      dispatchServer({
        type: "response.output_audio.delta",
        response_id: responseId,
        delta: "fixture-audio",
        turn_id: turnId,
      });
      dispatchServer({
        type: "response.output_audio.done",
        response_id: responseId,
        turn_id: turnId,
      });
      dispatchServer({ type: "response.done", response_id: responseId, turn_id: turnId });
      dispatchServer({ type: "assistant_speech_ended", response_id: responseId, turn_id: turnId });
      if (window.__kikioriVoiceTest.pendingResponseContext?.turnId === turnId) {
        window.__kikioriVoiceTest.pendingResponseContext = null;
      }
    }

    class FakeDataChannel {
      constructor() {
        this.label = "oai-events";
        this.readyState = "open";
        queueMicrotask(() => emit(this, "open"));
      }

      addEventListener(type, listener) {
        on(this, type, listener);
      }

      close() {
        this.readyState = "closed";
        emit(this, "close");
      }

      send(value) {
        const event = JSON.parse(value);
        sentEvents.push({ direction: "client", event });
        if (event.type === "session.instructions.append") {
          queueMicrotask(() => dispatchServer({
            type: "session.instructions.appended",
            event_id: event.event_id,
            session: { id: configuredSessionId },
          }));
        } else if (event.type === "session.commentary.append") {
          queueMicrotask(() => emitAssistantResponse(
            event.content === "少々お待ちください。"
              ? event.content
              : "こんにちは。最初の質問をお願いします。",
          ));
        } else if (event.type === "response.create") {
          const responseContext = window.__kikioriVoiceTest.pendingResponseContext ?? {};
          queueMicrotask(() => emitAssistantResponse(
            "ありがとうございます。次の質問です。",
            "e2e-response-" + (responseContext.turnId ?? Date.now()),
            responseContext,
          ));
        }
      }
    }

    class FakeRTCPeerConnection {
      constructor() {
        this.connectionState = "new";
        this.iceGatheringState = "complete";
        this.localDescription = null;
      }

      addEventListener(type, listener) {
        on(this, type, listener);
      }

      addTrack(track) {
        window.__kikioriVoiceTest.audioTrackLive = track?.readyState === "live";
      }

      createDataChannel() {
        const channel = new FakeDataChannel();
        channels.push(channel);
        queueMicrotask(() => dispatchServer({
          type: "session.started",
          session: { id: configuredSessionId },
        }));
        return channel;
      }

      async createOffer() {
        return { type: "offer", sdp: "v=0\r\n" };
      }

      async setLocalDescription(description) {
        this.localDescription = description;
      }

      async setRemoteDescription() {
        this.connectionState = "connected";
        emit(this, "connectionstatechange");
      }

      close() {
        this.connectionState = "closed";
        emit(this, "connectionstatechange");
      }
    }

    window.__kikioriVoiceTest = {
      sentEvents,
      serverEvents,
      dataChannelErrors: [],
      audioTrackLive: false,
      pendingResponseContext: null,
      emit(event) {
        dispatchServer(event);
      },
      emitTurn({ transcript, assessment, nextQuestion }) {
        const turn = assessment?.turn_id ?? "e2e-turn-" + Date.now();
        const delegationId = assessment?.delegation_id ?? "e2e-delegation-" + turn;
        const responseId = "e2e-delegated-response-" + turn;
        window.__kikioriVoiceTest.pendingResponseContext = {
          turnId: turn,
          delegationId,
          nextQuestion: nextQuestion ?? null,
        };
        dispatchServer({
          type: "input_audio_buffer.speech_started",
          session: { id: configuredSessionId },
        });
        dispatchServer({
          type: "conversation.item.input_audio_transcription.delta",
          delta: transcript,
        });
        dispatchServer({
          type: "conversation.item.input_audio_transcription.completed",
          item_id: "e2e-item-" + turn,
          transcript,
          turn_id: turn,
        });
        dispatchServer({
          type: "session.delegation.created",
          delegation: { id: delegationId },
          response_id: responseId,
          session: { id: configuredSessionId },
        });
        dispatchServer({
          type: "response.output_item.done",
          delegation_id: delegationId,
          response_id: responseId,
          item: {
            type: "function_call",
            name: "update_interview_state",
            call_id: assessment?.call_id ?? "e2e-call-" + turn,
            arguments: JSON.stringify(assessment ?? {}),
          },
        });
      },
    };
    window.RTCPeerConnection = FakeRTCPeerConnection;
  }, { sessionId });
}

export async function readFakeLiveEvents(page) {
  return page.evaluate(() => window.__kikioriVoiceTest?.serverEvents ?? []);
}

export async function readFakeLiveDiagnostics(page) {
  return page.evaluate(() => ({
    events: window.__kikioriVoiceTest?.serverEvents ?? [],
    dataChannelErrors: window.__kikioriVoiceTest?.dataChannelErrors ?? [],
  }));
}
