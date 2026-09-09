import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/features/realtime-voice/realtimeAssistantMessage.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { realtimeAssistantMessage } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const session = { id: "s1", initialReplyText: "これから始めます。お名前を教えてください。" };
const initial = { kikiori_kind: "initial", kikiori_response_id: "initial-response-s1", kikiori_turn_id: "initial-s1" };

test("initial transcript updates the canonical message without replacing its full text", () => {
  const messages = new Map([["initial-response-s1", { text: session.initialReplyText }]]);
  for (const text of ["これから", "これから始めます。", "お名前を教えてください。"] ) {
    const message = realtimeAssistantMessage(session, initial, text);
    messages.set(message.voiceResponseId, message);
  }
  assert.equal(messages.size, 1);
  assert.equal(messages.get("initial-response-s1").text, session.initialReplyText);
});

test("missed response.created does not insert a second message under the OpenAI id", () => {
  assert.equal(realtimeAssistantMessage(session, undefined, "最初の文"), undefined);
  const done = realtimeAssistantMessage(session, initial, "最初の文です。");
  assert.equal(done.voiceResponseId, "initial-response-s1");
});

test("a normal response is never guessed to be initial, even if its text is identical", () => {
  const message = realtimeAssistantMessage(session, {
    kikiori_kind: "interview", kikiori_response_id: "r2", kikiori_turn_id: "t2",
  }, session.initialReplyText);
  assert.equal(message.voiceResponseId, "r2");
  assert.equal(message.voiceTurnId, "t2");
});
