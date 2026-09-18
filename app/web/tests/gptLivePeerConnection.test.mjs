import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/features/realtime-voice/webrtc/gptLivePeerConnection.ts", import.meta.url),
  "utf8",
);
const hookSource = await readFile(
  new URL("../src/features/realtime-voice/hooks/useGPTLiveVoiceConversation.ts", import.meta.url),
  "utf8",
);

function position(text) {
  const index = source.indexOf(text);
  assert.notEqual(index, -1, `missing ${text}`);
  return index;
}

test("GPT-Live WebRTC setup follows the required browser order", () => {
  const order = [
    'const peerConnection = new RTCPeerConnection();',
    'peerConnection.addEventListener("track"',
    'navigator.mediaDevices.getUserMedia({ audio: true })',
    "peerConnection.addTrack(track",
    'peerConnection.createDataChannel("oai-events")',
    'dataChannel.addEventListener("message"',
    "peerConnection.createOffer()",
    "peerConnection.setLocalDescription(offer)",
    "waitForIceGatheringComplete(peerConnection",
    "createGPTLiveSession(offerSdp, options.signal)",
    'peerConnection.setRemoteDescription({',
    "waitForSessionStarted(sessionStarted",
  ].map(position);

  assert.deepEqual(order, [...order].sort((left, right) => left - right));
  assert.match(source, /type: "answer"/);
});

test("GPT-Live DataChannel is not used for audio or legacy turn control", () => {
  assert.doesNotMatch(source, /dataChannel\.send\s*\(/);
  assert.doesNotMatch(source, /session\.input_audio\.append/);
  assert.doesNotMatch(source, /session\.output_audio\.delta/);
  assert.doesNotMatch(source, /["']session\.start["']/);
  assert.doesNotMatch(source, /speech_stopped|response\.completed|response\.done|process_turn/);
});

test("GPT-Live readiness and transport failures are observable", () => {
  assert.match(source, /createGPTLiveSession\(offerSdp, options\.signal\)/);
  assert.match(source, /gpt_live_session_started_timeout/);
  assert.match(source, /void sessionStarted\.catch\(\(\) => undefined\)/);
  assert.match(source, /waitForMediaStream\(microphonePromise, options\.signal\)/);
  assert.match(source, /stream\.getTracks\(\)\.forEach\(\(track\) => track\.stop\(\)\)/);
  assert.match(source, /handleDataChannelEvent\(\{ type: "transport\.closed" \}\)/);
  assert.match(source, /gpt_live_transport_closed/);
  assert.match(source, /code: "data_channel_error"/);
});

test("GPT-Live never exposes the server API key to browser code", () => {
  assert.doesNotMatch(source, /OPENAI_API_KEY/);
  assert.doesNotMatch(source, /Authorization:\s*`Bearer\s+sk-/);
});

test("GPT-Live transcript deltas stay observational", () => {
  assert.match(hookSource, /gpt_live_input_transcript_delta/);
  assert.match(hookSource, /gpt_live_output_transcript_delta/);
  assert.doesNotMatch(hookSource, /setPartialTranscript|speech_stopped|response\.completed|response\.done|process_turn/);
});
