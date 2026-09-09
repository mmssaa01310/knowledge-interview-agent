# Conversation Algorithm 現行監査・仕様化

更新日: 2026-09-09
対象: `text` / `transcribe_polly` / `nova_sonic` / `openai_realtime`
状態: 現行コードの監査、characterization、Canonical Intent Policy Phase 1、実LLM評価、Single State Writer Phase 2を反映。

この文書は、理想的な会話Policyではなく、現在のコードが実際にどこで判断し、どこでStateを変更し、どこで出力を開始するかを記録する。行番号は本監査時点のものを示す。LLMの実際の分類は入力・モデル応答によって変わるため、コードが提供している分岐を確定事項、モデルが返す値を実行時観測事項として分けて扱う。

Phase 2.5 / Phase 3では、Stateの書き込み境界、Background proposalの順序保護、Provider source turnのDurable Dedup、Canonical Question Definitionの不変契約を実装した。Initial Questionの全面統合、Realtime音声経路の変更、RAG構造変更はこの文書の対象外である。

## 1. 監査結果の要約

`app/api` のText/Voice処理には、既存 `StructuredDialogueAct` を型として再利用する `Canonical Intent -> Canonical Action` Policyが実装されている。Provider側の入力境界にはまだ粗いtransport `turnType` が残るが、Voiceの会話処理開始時にはBackendのCanonical Policyを再評価する。

最も近い「Stateの正本」は `app/api` の `interview_state` と `VoiceTurn` である。しかし、会話制御の判断は次の実装に分散している。

| 判断 | 現在の主な実装 | 監査結果 |
|---|---|---|
| User Turnの受領・重複排除 | OpenAI sideband、Voice API、Transcribe runtime、Nova tool coordinator | OpenAIは`openai-{item_id}`、Novaは`nova-{tool_use_idまたはcompletion_id}`、Transcribeは受理したAWS Transcribeの`ResultId`（複数なら順序付きhash）を`clientTurnId`へ変換し、APIとDBのDurable契約で再利用する。ResultIdを提供しない旧/代替ストリームだけはruntime生成IDとなる |
| Turn Type | `InterviewBridge.process_turn()` の既定値、Voice intent API、OpenAI coordinator | OpenAI transportには粗い `ANSWER` が残るが、Backend側でCanonical Intentを再評価するため、transport値は会話Policyの正本ではない |
| dialogueAct / Canonical Intent | `services/conversation_policy.py:resolve_canonical_intent()` | 既存 `StructuredDialogueAct` を再利用。Routerは副作用なしで意図だけを返す |
| Canonical Action | `services/conversation_policy.py:resolve_canonical_action()` | Intentとpending confirmation等から1つのActionへ変換 |
| Fast判定 | `start_fast_interview_turn()` | `minimumInformationPresent`等のBooleanだけで、dialogueActを分類しない |
| State更新 | `services/interview_state_transition.py:commit_interview_state()` | 永続化入口は1系統。Fast foregroundはprovisionalな質問進行、Backgroundはversion付きproposalをCoordinator経由で安全にmergeする |
| 次target | `select_next_question_target()` | Backend coordinatorが決める。Question Generatorはtargetを決めない |
| Question definition | Field / question / questionPlanのsnapshot | `questionDefinition`とhashをQuestion snapshotへ保存し、`questionProgress`、`renderedQuestionText`、`lastExplanationText`相当の出力とは分離。説明・生成結果から定義へ書き戻さない |
| Question rendering | `_generate_question_text()`、Question Generator provider | Backendが選んだtargetの表現を生成する。RealtimeはPhase 1では読み上げる役割 |
| 初回質問 | `create_voice_session()` -> `_initialize_initial_question()`、OpenAIの `session.created` | 通常Turnとは別の初期化経路。OpenAIはsidebandで初期応答を送る |

したがって、会話処理では次の契約を正本として扱う。

1. Provider固有IDを、`voice_session_id + clientTurnId`で永続的に追跡できるCanonical User Turnへ一度だけ変換すること。
2. 1つのUser Turnから1つの会話分類、1つのCanonical Action、1つのState transitionだけを生成すること。
3. Interview Plan / Knowledge由来のCanonical Question Definitionを不変とし、質問文・説明文は一時的なrendered outputとして扱うこと。

Canonical Intent/Actionは `conversation_policy.py`、永続StateのcommitとBackground mergeは `interview_state_transition.py` が担当する。VoiceTurnのDurable Dedupと処理claimは`voice_turn_repository.py`および`voice_turn_session_client_id_unique_idx`が担当する。

## 2. 現在の全体フロー

### 2.1 Text

```text
Browser text input
  ↓ app/web/src/routes/useKnowledgeWorkspaceController.ts:1200-1267
POST /api/records/{record_id}/messages
  ↓ app/api/src/ai_interviewer_api/routers/records.py:244-363
User message保存・turnType決定
  ↓ app/api/src/ai_interviewer_api/routers/records.py:431-452
generate_interview_reply()
  ↓ app/api/src/ai_interviewer_api/services/ai_interview.py:61-115
knowledge取得
  ↓
generate_structured_interview_result()
  ↓ app/api/src/ai_interviewer_api/agents/interview_knowledge/service.py:281-357
Structured Interpreter / State apply / target selection / RAG / Question Generator
  ↓
reply textをSSEへ分割してBrowserへ返却
  ↓ app/api/src/ai_interviewer_api/services/ai_interview.py:61-115
```

Text入口ではVoiceのFast Pathは通らない。Fast flagを参照する `_process_structured_voice_turn()` はVoice API経路に限られる（`voice_interview.py:625-705`）。

### 2.2 `transcribe_polly`

```text
Browser WebRTC audio track
  ↓ app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/runtime.py:335-359
独自VAD + 100ms単位のTranscribe送信
  ↓ _on_transcribe_result():604-635
partial/final transcript保持
  ↓ _endpoint_loop():661-683
endpoint silence + final settle
  ↓ _finalize_user_turn():711-739
UserSpeechEnded / ANSWER_PROCESSING
  ↓ asyncio.create_task(_process_interview_turn())
InterviewBridge.process_turn_stream()
  ↓ app/voice/src/ai_interviewer_voice/services/interview_bridge.py:180-232
POST /internal/voice-sessions/{id}/turns
POST /internal/voice-sessions/{id}/turns/{turn_id}/process-stream
  ↓ app/api/src/ai_interviewer_api/routers/internal_voice.py:31-126
_process_voice_turn() -> _process_structured_voice_turn()
  ↓ app/api/src/ai_interviewer_api/services/voice_interview.py:555-617, 625-904
FastまたはStructured -> RAG -> Question Generator -> reply text
  ↓ process-streamのstarted/delta/complete
_play_streaming_formal_reply()
  ↓ runtime.py:1160-1257
PollyTextChunker + Polly worker pool
  ↓ _synthesize_streaming_chunks():1259以降
PCM output -> Browser audio
```

Transcribe側は、partialをUIに出すが、Backend Turnを作るのはendpointで `_finalize_user_turn()` が作る処理タスクだけである。Transcribe final自体はRAGを開始しない（`runtime.py:604-635`）。

### 2.3 `nova_sonic`

```text
Browser WebRTC audio
  ↓ runtime.py:385-454
Bedrock Nova Sonic bidirectional stream
  ↓ protocol dispatcher
User transcript + forced tool use
  ↓ tool_turn_coordinator.py:98-166
条件成立時に process_interview_bridge_turn() を create_task
  ↓ tool_turn_coordinator.py:271-361
InterviewBridge.save_turn() -> process_saved_turn()
  ↓ app/voice/services/interview_bridge.py:154-152 / API
app/apiのVoice Interview処理
  ↓
Tool resultをNovaへ返す
  ↓
Nova Sonic audio output -> Browser
```

共通runtime factoryは `nova_sonic` と `transcribe_polly` を生成する（`app/voice/src/ai_interviewer_voice/services/runtime_factory.py:21-99`）。`openai_realtime` はこのfactoryの分岐ではなく、WebRTC router/coordinatorで別に生成される。

### 2.4 `openai_realtime`

```text
Browser microphone
  ↓ app/web/src/features/realtime-voice/webrtc/openaiRealtimePeerConnection.ts:25-126
createOffer / setLocalDescription / ICE待ち
  ↓ useRealtimeVoiceInterview.ts:562-600
POST /voice/webrtc/{voice_session_id}/openai-offer
  ↓ app/voice/src/ai_interviewer_voice/routers/webrtc.py:147-204
OpenAIRealtimeCoordinator.create_offer()
  ↓ webrtc.py:604-639
OpenAIRealtimeCallClient.create_call()
  ↓ app/voice/src/ai_interviewer_voice/runtimes/openai_realtime/client.py:29-89
POST https://api.openai.com/v1/realtime/calls
multipart sdp + session
  ↓
SDP answerをBrowserへ返却
  ↓ Browser setRemoteDescription()
OpenAI Realtime Session
  ├─ Browser WebRTC: audio input / audio output / DataChannel event受信
  └─ app/voice sideband: session update / transcript / Backend処理 / response.create
        ↓ coordinator.py:116-285
conversation.item.input_audio_transcription.completed
        ↓ _process_turn():324-408
InterviewBridge.process_turn(turn_type="ANSWER")
        ↓ app/voice/services/interview_bridge.py:116-152
save_turn -> process_saved_turn
        ↓ HTTP app/api
_process_voice_turn -> _process_structured_voice_turn
        ↓ app/api/services/voice_interview.py:555-904
FastまたはStructured -> RAG -> Question Generator全文完了
        ↓
reply_ready
        ↓ coordinator.py:401-408
response.create(input=[reply_text], output_modalities=["audio"])
        ↓ coordinator.py:410-458
response.output_audio.delta
        ↓ Browser DataChannel / remote audio track
Audio element playback
```

OpenAIのBrowser側 `conversation.item.input_audio_transcription.completed` はUI更新だけで、Backend処理を直接呼ばない（`useRealtimeVoiceInterview.ts:187-216`）。Backend処理の契機はsideband側の同名eventだけである（`coordinator.py:201-221`）。

## 3. 責務とDecision Ownership

以下が現行実装の責務表である。`foreground` はそのTurnのreply生成前に待たれる処理、`background` はreply commit後も継続し得る処理を示す。

| 責務 | file / function / line | 入力 | 出力 | State変更 | 前景/背景 | Provider |
|---|---|---|---|---|---|---|
| Voice session作成 | `app/api/src/ai_interviewer_api/services/voice_interview.py:create_voice_session()` 132-177 | record, provider | VoiceSession, initial question | VoiceSession、initial state | 前景 | 全Voice |
| 初回質問生成 | `voice_interview.py:_initialize_initial_question()` 1101-1132 | record, knowledge, fields, state | greeting + question | initial question / session snapshot | 前景 | 全Voice session開始 |
| OpenAI call作成 | `app/voice/.../openai_realtime/client.py:create_call()` 29-89 | SDP, session payload | call id, SDP answer, Location | OpenAI call | 前景 | OpenAI |
| OpenAI sideband開始 | `app/voice/.../openai_realtime/coordinator.py:OpenAIRealtimeSession.start()` 78-99、`_run_sideband()` 116-167 | call id, key | WebSocket/event loop | in-memory session | 背景task | OpenAI |
| User transcript final受領 | `coordinator.py:_handle_event()` 201-221 | item_id, transcript | turn task | `_processed_transcript_items` | 前景task起動 | OpenAI |
| OpenAI User Turn type | `coordinator.py:_process_turn()` 350-365 | transcript, current q | Bridge呼出し | なし | 前景 | transport値は粗い `ANSWER`。BackendのCanonical Policyで再評価 |
| 任意VoiceのTurn type分類 | `app/voice/.../services/interview_bridge.py:process_turn()` 116-152、`interview_api.py:217-246` | transcript, current q | `ANSWER`/`CONTROL` | なし | 前景 | Bridge callerが`None`の時 |
| Turn ID reuse | `app/api/.../services/voice_interview.py:create_voice_turn()` 258-359 | clientTurnId, transcript | VoiceTurn | VoiceTurn / sequence | 前景 | 全Voice API |
| Turn lifecycle/idempotency | `voice_interview.py:_process_voice_turn()` 555-617 | voice_session_id, turn_id | process result | processing/EVALUATING/COMMITTED/failed | 前景 | 全Voice |
| Dialogue Act | `app/api/.../agents/interview_knowledge/schemas.py:27-39`、Structured provider呼出し `service.py:1285-1311` | full interpreter context | `StructuredInterviewOutput.dialogueAct` | 後続applyでStateへ記録 | 前景（Fast時は背景） | Full Structured |
| Fast answer sufficiency | `service.py:start_fast_interview_turn()` 402-693、Fast schema 6-19 | current q, latest utterance, q definition | 3 Boolean + `needsQuestionExplanation` | provisional target/response | 前景 | Voice flagがONのVoice |
| Full answer sufficiency | `service.py:_generate_structured_interview()` 1168-1911 | full context, current q | answer assessment, updates, act | State apply | 前景またはFast背景 |
| Transcript correction | `service.py:1332-1380` | transcriptAssessment | normalized/correction status | user message / pending transcript confirmation | Full Structured | 全ProviderがAPI経由で使用 |
| Clarification / prompt explanation | `service.py:1396-1425`、`_keep_current_question()` 1914-1963 | `QUESTION_TO_ASSISTANT` / `CLARIFICATION_REQUEST` | help text, current question | current target維持 | Full Structured |
| Hesitation / backchannel | `service.py:1427-1445` | `HESITATION` / `BACKCHANNEL` / `OTHER` | localized retry/wait text | current target維持 | Full Structured |
| Confirmation | `service.py:1272-1284`、`coordinator.py:_confirm_target()` 1881-1978 | awaiting target + unambiguous confirmation | synthetic `CONFIRMATION` or full output | field/requirement `CONFIRMED` | Full Structured |
| Rejection | `coordinator.py:_reject_target()` 2056-2123、service primary branch 1550-1669 | `REJECTION` | pending candidate reset | target `UNANSWERED`等 | Full Structured |
| Field / requirement apply | `coordinator.py:apply_structured_output()` 457-666、`_apply_field_update()` 2125-2215 | fieldUpdates / requirementUpdates | changed topics | candidate / awaiting / confirmed | Full Structured |
| Candidate / probe | `coordinator.py:probe_register` 1002-1090、`service.py:1508以降` | incomplete / candidate | probe/candidate | pending state | Full Structured |
| Completion | `coordinator.py:evaluate_completion()` 669-697 | contradictions, pending, required, closing | complete bool | completion判定 | Full Structured |
| Next target | `coordinator.py:select_next_question_target()` 700-879 | current State, fields, profile | exactly one target | active clarification/current target等 | 前景 | Text/Voice共通API |
| Question definition | `service.py:_question_definition_context()` 2255-2347 | selected target/field | title, originalQuestion, description, required items等 | なし | 前景 | QG |
| Question wording | `service.py:_generate_question_text()` 2669-2905、provider `_question_system_prompt()` 618-638 | selected target + definition + bounded context | questionText | askedQuestions when commit | 前景 |
| Backend reply送信 | OpenAI `coordinator.py:_send_backend_reply()` 410-458 | committed `reply_text` | Realtime `response.create` | active response flag | 前景の最後 | OpenAI |
| UI User message | `app/web/.../useRealtimeVoiceInterview.ts:187-216` | Browser DataChannel completed | user message | React message state | UI | OpenAI |
| UI assistant merge | `useRealtimeVoiceInterview.ts:231-276`、`useKnowledgeWorkspaceController.ts:1270-1285,1531-1589` | transcript delta/done + metadata | streaming/completed message | React message state | UI | OpenAI/共通 |

### 3.1 正本の評価

「Stateの保存先」と「会話Policy」の正本は `app/api` に置かれている。Phase 1以降の通常のText/Voice turnでは、次の順に一度だけ決定する。

* `services/conversation_policy.py:resolve_canonical_intent()` が既存 `StructuredDialogueAct` をCanonical Intentとして返す。
* `services/conversation_policy.py:resolve_canonical_action()` がIntentと現在StateからCanonical Actionへ変換する。
* `voice_interview.py:_process_voice_turn()` はVoice providerから渡された粗いtransport情報をそのまま意味分類には使わず、Canonical Policyを呼ぶ（明示的なCONTROL turnを除く）。
* `ai_interview.py:_resolve_text_turn_policy()` も同じPolicyを呼ぶ。
* Full Structured Interpreterの `dialogueAct` は検証・Telemetryであり、Background結果でCanonical Actionを後から変更しない。
* Fast Pathは `canonicalAction == PROCESS_ANSWER` の場合だけ回答十分性を判定する。
* Background Structuredは `persist_state=False` の提案生成後、`interview_state_transition.py:apply_background_state_proposal()` を介して、最新Stateへ抽出結果だけをmergeする。
* 永続Stateの実際の保存は `interview_state_transition.py:commit_interview_state()` に集約され、Backgroundはcurrent target、next target、canonical intent/actionを上書きしない。

Provider側のUI表示とtransport Turn typeはまだ別の境界にある。一方、source IDを持つVoice Turnについては、Backendの`voice_session_id + clientTurnId`をDB unique indexで保護し、重複要求を既存行へ再利用するDurable契約を実装している。Transcribeも受理した`ResultId`をruntimeイベントとAPIの両方へ引き継ぐため、ResultIdが存在する通常経路では再接続後も同じsource identityを復元できる。ResultIdを提供しないストリームではprovider側に安定IDがないため、runtime生成IDとなる。

## 4. 現行State Machineの復元

### 4.1 Interview State

初期Stateは `build_initial_structured_state()` が作り、`status`、`stateVersion`、`currentFieldId`、`currentQuestionId`、`askedQuestions`、`fieldStates`、`requirementStates`、`activeProbeTarget`、`pendingTranscriptConfirmation`、`clarificationQueue`、`activeClarificationRequest`、`tentativeCandidates`、`contradictions`、`openIssues`、`closingState`等を持つ（`coordinator.py:119-185`）。

主要な値は次のとおりである。

| State領域 | 現行値・構造 | 根拠 |
|---|---|---|
| Interview status | `in_progress` / `completed`等 | `coordinator.py:119-185`、`voice_interview.py:555-617` |
| Field answer state | `UNANSWERED`、`CANDIDATE_PENDING`、`AWAITING_CONFIRMATION`、`CONFIRMED`を含む | `coordinator.py:2125-2215`, `1881-1978`, `2056-2123` |
| Answer resolution | `TENTATIVE`、`AUTO_CONFIRM`、`CONFIRMED`等の正規化値 | `schemas.py`、`coordinator.py:apply_structured_output()` |
| Closing state | 初期 `UNANSWERED`、完了条件は `CONFIRMED` | `coordinator.py:669-697`, `700-879` |
| Transcript correction | `pendingTranscriptConfirmation`、correction status | `service.py:1332-1380`, `coordinator.py:700-763` |
| Clarification | queue -> active -> history | `coordinator.py:882-955`, `700-750` |
| Probe | active probe / probe history等 | `coordinator.py:1002-1090`, `service.py:1508以降` |
| Candidate | tentative candidates / pending confirmations | `coordinator.py:700-879`, `service.py:1550-1669` |
| Detailed findings | contradictions / openIssues / applicability | `coordinator.py:619-666` |

### 4.2 Field / target transition

| FROM | Event / condition | TO | 更新実装 |
|---|---|---|---|
| 初期State | required target選択 | `currentQuestionId` / target設定 | `coordinator.py:700-879`、`service.py:1798-1825` |
| `UNANSWERED` | valid field update、まだ確定不可 | `CANDIDATE_PENDING` または `AWAITING_CONFIRMATION` | `coordinator.py:_apply_field_update()` 2125-2215 |
| `UNANSWERED` / candidate | all required itemが揃いauto confirm可能 | `CONFIRMED` | `coordinator.py:_confirm_target()` 1881-1978、`apply_structured_output()` 457-666 |
| `AWAITING_CONFIRMATION` | unambiguous `CONFIRMATION` | `CONFIRMED` | `service.py:1272-1284` -> `coordinator.py:_confirm_target()` |
| `AWAITING_CONFIRMATION` | `REJECTION` | `UNANSWERED`等へ戻し候補破棄 | `coordinator.py:_reject_target()` 2056-2123 |
| 任意target | `QUESTION_TO_ASSISTANT` / `CLARIFICATION_REQUEST`かつupdateなし | current target維持、説明文 | `service.py:1396-1425` -> `_keep_current_question()` 1914-1963 |
| 任意target | `HESITATION` / `BACKCHANNEL` / `OTHER` | current target維持、再開促進文 | `service.py:1427-1445` -> `_keep_current_question()` |
| 任意target | transcript `UNCERTAIN` | current target維持、transcript retry | `service.py:1380-1395` -> `_keep_current_question()` |
| valid answer | insufficient | current/active probeまたはfollow-up target | `service.py:1469-1506`, `coordinator.py:700-879` |
| valid answer | sufficient | next target selection | `service.py:1550-1825`、`coordinator.py:700-879` |
| no unresolved issue | required/closing等が未完了 | next target | `coordinator.py:evaluate_completion()` 669-697, `select_next_question_target()` 700-879 |
| all completion conditions | closing `CONFIRMED`等 | `completed` | `service.py:1764-1781`、`voice_interview.py:625-904` |

### 4.3 VoiceTurn lifecycle

`VoiceTurn`はAPIで `clientTurnId` を検索し、同じtranscriptとstate versionなら既存行を返す（`voice_interview.py:258-359`）。処理時は `processingStatus="processing"`、`lifecycleStatus="EVALUATING"` としてから、成功時にcommit結果を保存する（`voice_interview.py:555-617`）。既に `COMMITTED` のTurnは保存済み結果を返す（同:566-569）。

これはAPI内の冪等性であり、OpenAIの `item_id` を専用カラムで永続化するExactly-once契約ではない。OpenAI側は `clientTurnId="openai-{item_id}"` に変換しているだけである（`coordinator.py:356-364`）。

### 4.4 VoiceSession / Runtime / UIの状態

会話Stateとは別に、接続と再生の状態が存在する。

| State領域 | 現行値・遷移 | 更新箇所 |
|---|---|---|
| VoiceSession | 作成時にactive系のsessionを保存し、停止時に `status="stopped"`、`connectionStatus="closed"` | `voice_interview.py:132-177, 247-255` |
| OpenAI session | `_closed` / `_finished`、active response pending、active response id | `openai_realtime/coordinator.py:35-76, 231-276` |
| OpenAI response | Browser側は `response.created` -> `preparing_audio` -> audio deltaで `speaking` -> `response.done`で`listening`等 | `useRealtimeVoiceInterview.ts:218-316` |
| Transcribe input | `ANSWER_LISTENING` -> `ANSWER_PROCESSING`、formal reply中はinput gateを閉じ、playback drain後に再開 | `transcribe_polly/runtime.py:661-739, 1160-1257` |
| Browser hook | `checking`, `connecting`, `listening`, `finalizing_transcript`, `processing_interview`, `preparing_audio`, `speaking`, `interrupted`, `completed`, `error`, `disconnected`等 | `app/web/src/features/realtime-voice/types.ts:16-34`, `useRealtimeVoiceInterview.ts:488-705` |
| Assistant output | OpenAIではRealtime responseの状態、Transcribeでは `PLANNED` / `SYNTHESIZING` / interrupted等をruntimeが管理 | `openai_realtime/coordinator.py:231-276`, `transcribe_polly/runtime.py:1035-1158` |

これらは会話PolicyのStateと同じではない。例えばBrowserが `processing_interview` になったこと自体は、Backend Stateが `EVALUATING`になったことを意味しない。現行はUI、transport、BackendのStateをIDで関連付けているが、1つのCanonical Conversation State objectとしては公開していない。

## 5. 実例のState Trace

### 5.1 Characterization harness

現行アルゴリズムを変更前後で観測できるよう、次を追加した。

* `app/api/tests/characterization/conversation_algorithm_harness.py:25-466`
* `app/api/tests/characterization/test_conversation_algorithm_characterization.py:19-101`

これは本番Routerを実装せず、`generate_structured_interview_result()` に決定論的Providerを注入して、現行のStructured service / coordinatorの出力を記録する。`routerIntent`と`fastDecision`は現行にRouterがないため `N/A` として出力する（harness.py:34-39, 356-375）。

出力項目は次のとおり。

```json
{
  "input": "...",
  "routerIntent": "N/A",
  "fastDecision": "N/A",
  "structuredDialogueAct": "...",
  "stateBefore": {},
  "stateAfter": {},
  "currentTarget": {},
  "nextTarget": {},
  "action": "...",
  "generatedQuestion": "...",
  "questionDefinitionSeenByGenerator": {},
  "stateChanged": true
}
```

現在追加したcharacterizationは次を確認する。

* 正常回答で `ANSWER`、profileのrequired itemsを適用し、次のfieldへ進む。
* `AWAITING_CONFIRMATION` に対する「大丈夫です。」が `CONFIRMATION` -> `CONFIRMED`になる。
* `QUESTION_TO_ASSISTANT`、`REJECTION`、`HESITATION`、`CORRECTION`をFull Structured出力として記録し、current targetを維持する。
* 同じOpenAI source itemを `clientTurnId` に変換したAPI保存が1 VoiceTurnを再利用する。

検証結果:

```text
tests/characterization/test_conversation_algorithm_characterization.py
7 passed
```

これはAPIの同一 `clientTurnId` 再利用と、既存Structured serviceの決定分岐を検証するもので、実Browserでsideband eventが何回届いたかを測るE2Eではない。

### 5.2 Case A: 正常回答 -> Confirmation

入力を2Turnに分けた場合のコード上の成立条件は次のとおり。

```text
Turn 1: 宮崎です。スマート技術開発部にいて、エンジニアをやっています。
  ↓ Structured output: ANSWER + fieldUpdates
  ↓ coordinator.apply_structured_output()
  ↓ required item / candidate / confirmation条件をStateへ適用
  ↓ current targetが確認対象なら確認質問を生成

Turn 2: 大丈夫です。
  ↓ current targetが awaiting confirmation
  ↓ is_unambiguous_confirmation()
  ↓ synthetic StructuredDialogueAct=CONFIRMATION
  ↓ _confirm_target()
  ↓ select_next_question_target()
```

「大丈夫です。」の明示的な処理は `service.py:1272-1284` にある。ただしこの分岐は、処理時点の `current_question` / Stateが確認対象として認識されていることが前提である。別のtarget、provisional state、state version conflict、または別Turnとして入力された場合に同じ分岐になるとはコードだけから断定できない。

従って「確認後に同じ基本プロフィールへ戻る」事象は、まず同じTurn IDと `stateBefore/stateAfter/nextTarget` をこのharnessまたは実E2Eログで取得してから根因を確定すべきであり、現行コードから単一の原因と断定していない。

### 5.3 Case B: 質問の意味を聞き返す

Full Structured経路では、`QUESTION_TO_ASSISTANT` または `CLARIFICATION_REQUEST` で構造化更新がない場合、`service.py:1396-1425` が `questionDefinition` を作り、`localized_interview_question_help()` を返し、`_keep_current_question()` へ入る。したがってtargetを進めず、現在質問の説明へ進むコードは存在する。

一方Fast経路では、Fast schemaは `needsQuestionExplanation` を持つが、dialogueActそのものを持たない（`fast_interpreter/schemas.py:6-19`）。`start_fast_interview_turn()` は `can_proceed(assessment) and not needs_question_explanation` で進行可否を決める（`service.py:512-523`）。Falseの場合の説明文は `question_definition` のoriginal question、description、required itemsを使う（同:534-573）。よってFastで「質問の意味」を拾えるかは、Boolean `needsQuestionExplanation` のモデル出力に依存し、Full StructuredのdialogueAct分類とは別契約である。

### 5.4 Case C: 「すでに回答している」

OpenAI coordinatorは全completed transcriptをtransport上 `turn_type="ANSWER"` で送る（`coordinator.py:356-364`）。ただしVoice APIはその値だけで会話Actionを決めず、Canonical Intent Policyで再評価する。Textや、Bridge callerがtypeを省略する経路でも同じPolicyへ入る。旧来のAPI入口に残る `ANSWER` / `CONTROL` 2値分類は `QUESTION_TO_ASSISTANT`、`CORRECTION`等を表すCanonical Intentではない（`voice_interview.py:184-244`、`interview_api.py:217-246`）。

Full Structuredに到達すれば、Structured `dialogueAct` と `transcriptAssessment` により correction / clarification / retry 分岐がある。しかしOpenAIでは入口のtype固定により、Voice intent APIは呼ばれない。実際に「すでに回答している」がどのdialogueActになったかは、同じ入力をFull providerへ渡した実行traceが必要である。

### 5.5 Case D: Confirmation拒否

`REJECTION` がFull Structured出力として返り、対象がpending confirmationなら、`_reject_target()` が候補を破棄し、対象を未確定側へ戻す（`coordinator.py:2056-2123`）。この後のtargetは `select_next_question_target()` の優先順に従う（`coordinator.py:700-879`）。Fast foregroundだけでは `REJECTION`を表すschemaがないため、Fast ON時の最終的な拒否処理はBackground StructuredまたはFast FAIL側の挙動に依存する。

### 5.6 Case E: Hesitation

Full Structuredでは `HESITATION`、`BACKCHANNEL`、`OTHER` が `service.py:1427-1445` で `_keep_current_question()` に送られるため、target維持の応答になる。Fast schemaにはHESITATIONという型がなく、`clearlyIncomplete`等のBooleanに落ちる可能性がある。これがFast ON時の会話制御上のGapである。

### 5.7 Case F: 初回質問

Session作成時にAPIが `_initialize_initial_question()` を呼び、既存current questionがあれば再利用し、なければ `generate_interview_reply(..., persist=False)` で初回文を作る（`voice_interview.py:115-164, 1282-1313`）。Canonicalなcurrent questionが存在する場合はその質問文を再利用し、初回質問のために追加LLMを呼ばない。OpenAI側はsidebandの `session.created` を受けた時に `_send_initial_reply()` をtask化する（`coordinator.py:197-224, 340-403`）。

OpenAIの起動状態はTransportとConversationで分離する。sideband接続後も、`session.created`、`initialReplyText`確認、初回 `response.create`送信を順に完了するまで `READY_FOR_USER_TURN` にはしない（`coordinator.py:62-70, 193-224, 589-610`）。その間に届いたUser Turnは `_pending_first_user_turns` に保持され、初回応答送信後に通常処理へ進む（`coordinator.py:407-452`）。FrontendはVoice Session作成レスポンスの`initialReplyText`を先に表示し、Realtimeの初回transcript deltaは固定の初回応答IDへmergeする（`useRealtimeVoiceInterview.ts:153-182, 639-661`）。

### 5.8 Case traceの比較可能な項目

同じ入力をProviderやFast設定間で比較する際は、表示文だけでなく次の列を1 Turn単位で記録する。現在のharnessの `stateProjection` はこのうちStateに関する列を保持する。

| Case | currentTarget | dialogueAct / fast | fieldUpdates / answerResolution | pendingCandidate | state before -> after | next target / question |
|---|---|---|---|---|---|---|
| A normal answer | current profile target | Fullは `ANSWER`。FastはBooleanのみ | profile required items、candidate/auto-confirmの実値 | profile candidateの有無 | field stateの適用、必要ならawaiting/confirmed | `select_next_question_target()`の結果とQG文 |
| A confirmation | confirmation target | Canonical Routerは `CONFIRMATION`。Full validationは別に観測 | 通常のfield updateではなく `_confirm_target()` | candidate -> confirmed | `AWAITING_CONFIRMATION` -> `CONFIRMED` | 次field/target |
| B clarification | current question | Canonical Routerは `QUESTION_TO_ASSISTANT` / `CLARIFICATION_REQUEST`。Fastは呼ばない | 通常updateなし | 維持 | `_keep_current_question()` | current targetのdefinitionに基づく説明 |
| C already answered | current question | Canonical Routerで分類し、FullのdialogueActは検証として採取 | correction/answer updateの有無 | 維持または更新 | correctionStatus / Stateの変化 | currentまたはnext target |
| D rejection | confirmation target | Full `REJECTION`。Fast schemaには専用値なし | candidate rejection | candidate破棄 | `_reject_target()`で未確定側へ | 再回答用target |
| E hesitation | current question | Canonical Routerは `HESITATION` / `BACKCHANNEL`。Fastは呼ばない | 通常updateなし | 維持 | current target維持 | retry / current question |
| F initial | 未回答の初期target | APIが作った`initialReplyText`を表示し、OpenAIはsession.created -> initial task -> response.create | initial question definition / initial reply | なし | initial session snapshot + startup lifecycle | initial question text |

モデルが返す`dialogueAct`やfieldUpdatesは入力ごとに変わるため、上表の型は分岐契約であり、固定された実行結果ではない。Case A〜Eの具体的なJSONを得るには、実際のprovider responseをharnessの注入出力またはE2E traceへ記録する必要がある。

## 6. Provider差分監査

| Provider | 入力境界 | Turn生成契機 | Turn type | 会話処理 | 出力 |
|---|---|---|---|---|---|
| `text` | Browser HTTP | `POST /records/{id}/messages` | targetの有無から `ANSWER`/`CONTROL` | `generate_interview_reply()` -> Full Structured | HTTP/SSE |
| `transcribe_polly` | WebRTC audio -> Transcribe | endpoint + final settle -> `_finalize_user_turn()` | Bridge既定 `ANSWER`（別途stream API） | Voice API。Fast flag ONならFast foreground + Background | API stream -> Polly chunker -> PCM |
| `nova_sonic` | WebRTC audio -> Bedrock | transcript + forced tool conditions | `InterviewBridge.save_turn()`はanswer target | ToolTurnCoordinator -> Voice API | Nova tool result -> audio |
| `openai_realtime` | WebRTC audio -> OpenAI | sideband `input_audio_transcription.completed` | sidebandの粗いtransport値は `ANSWER`。意味分類はAPIのCanonical Policy | `process_turn()` -> Voice API -> `resolve_canonical_intent()`。Realtime側の会話Policyは未使用 | reply_textをsideband `response.create`へinput_textとして渡しRealtime audio |

`openai_realtime`は、Browserのcompleted eventでもUser UI messageを追加するが、Backend Turnはsidebandだけで作る（`useRealtimeVoiceInterview.ts:187-216`、`coordinator.py:201-221`）。従ってコード上の意図は二重Backend処理ではないが、UIのmergeとsidebandのAPI処理は別ID層である。

## 7. Fast Path ON / OFF差分

### OFF

`settings.structured_interview_fast_path_enabled` の既定値は `false`（`app/api/src/ai_interviewer_api/core/config.py:72-75`）。Voiceのeligible targetでは、`_process_structured_voice_turn()` が speculative retrievalを開始し、`generate_structured_interview_result()` をforegroundで呼ぶ（`voice_interview.py:656-705`）。Full interpreter、State apply、target選択、RAG resolve、Question Generator、commitまでがforegroundである。

### ON

同じflagがONで、closing / transcript confirmation / contradiction以外のcurrent questionなら、`start_fast_interview_turn()` を呼ぶ（`voice_interview.py:656-683`）。この関数は次の順で処理する。

```text
fields / profile / current target / question definitionを構築
  ↓ service.py:423-464
Background validationをThreadPool Futureへsubmit
  ↓ service.py:423-482, 695-837
Fast providerをThreadPoolへsubmit
  ↓ service.py:484-516
fast_future.result()だけをforegroundで待つ
  ↓ service.py:497-524
Fast PASSならprovisional state上でnext targetを選択
  ↓ service.py:575-598
final target用のspeculative retrievalを開始
  ↓ service.py:601-615
そのfutureをresolveしながらQuestion Generator全文を待つ
  ↓ service.py:616-669, _generate_question_text():2669-2905
foreground replyをcommit
  ↓ voice_interview.py:684-904
Backgroundは後でcallback経由でpersist/reconcile
  ↓ voice_interview.py:909-1055
```

Fastの設定は `global.openai.gpt-5.6-luna`、reasoning `none`、max output 160（`core/config.py:76-86`）。Full Structuredはmodel `global.openai.gpt-5.6-luna`、reasoning `low`、max output 6000。Question Generatorは同じmodel default、reasoning `none`、max output 600、streaming enabled default true（`core/config.py:93-114`）。

### Fast ON時の重要な差

* `Background Structured` は `background_future` としてFast futureと重なる。Fast future直後にbackgroundをawaitしていない（`service.py:482-524`）。
* しかしQuestion GeneratorはFast PASS後にforegroundで実行され、speculative retrieval futureを `resolve()` するため、RAG結果が必要ならそこで待つ（`service.py:601-629`, `_generate_question_text():2723-2779`）。
* Backgroundの結果はreplyを作らず、提案として `apply_background_state_proposal()` に渡される（`service.py:782-894`、`interview_state_transition.py:154-299`）。永続化はSingle Writer境界だけが行い、current target等の会話Policyフィールドは最新値を保持する。
* Fast schemaは `minimumInformationPresent`、`understandable`、`clearlyIncomplete`、`needsQuestionExplanation`、`reason`だけで、dialogueAct、fieldUpdates、confirmation、correction、contradiction、next targetを持たない（`fast_interpreter/schemas.py:6-19`）。Intent分類はFastではなくCanonical Routerが担当する。

### Dialogue Act別の比較

| 発話意図 | Fast OFF | Fast ON foreground | Background |
|---|---|---|---|
| `ANSWER` | Full dialogueAct + updates + sufficiency | BooleanでPASS/FAIL、PASSならprovisional advance | Full act/updateを後処理 |
| `CONFIRMATION` | awaiting targetならsynthetic confirmationまたはFull | Canonical ActionでFastを呼ばない | Fullで確認・State適用 |
| `REJECTION` | Fullでreject target | Canonical ActionでFastを呼ばない | Fullでreject可能 |
| `QUESTION_TO_ASSISTANT` / `CLARIFICATION_REQUEST` | help + current target維持 | Canonical ActionでFastを呼ばない | Fullでclarificationを検出・queue可能 |
| `CORRECTION` | transcript correction / field update | Canonical ActionでFastを呼ばない | Fullでcorrectionを検出 |
| `HESITATION` | current target維持 | Canonical ActionでFastを呼ばない | FullでHESITATIONを記録 |

Fast ONでも会話Intent/ActionはCanonical Policyが先に決める。Fastは `PROCESS_ANSWER` の回答十分性だけを担当し、詳細StructuredはBackground proposalとして後置される。

## 8. RAGとQuestion Generator

### 8.1 RAG

Full Structured OFFでは、`start_speculative_retrieval_for_interview_turn()` がcurrent questionを手掛かりにRetrieval futureを開始する（`service.py:360-399`）。その後 `_generate_question_text()` が最終target由来のqueryを作り、futureのquery/knowledge/tenant/limitが一致した場合だけ再利用する（`service.py:2669-2779`）。不一致なら正式検索へfallbackする。

Fast ONでは、Fast PASSでprovisional targetが決まった後、そのfinal target向けのRetrieval futureを開始し、同じ関数の `resolve()` をQuestion Generator呼出しの前段で実行する（`service.py:601-629`）。従って現在のFast経路は「FastとRAGを最初から常時並列」ではなく、Fast PASS後のtarget決定に続いてRAGを先行し、Question Generatorが結果を待つ構造である。

`retrievalPolicy=never` のtargetでは `_generate_question_text()` がRetrieval pathへ入らない（`service.py:2700-2706`）。

### 8.2 Question definitionの保持

現行コードではQuestion Generatorのcontextに、次の情報が残っている。

* `target`のid/type/label
* selected fieldの `name`、`description`、`questionText`、`aiQuestionExamples`、`questionPlan`、required/optional
* `questionDefinition`の `title`、`originalQuestion`、`description`、`purpose`、`requiredItems`、`optionalItems`、`completionCriteria`、missing/captured
* bounded state、直近6件のconversation、answerAssessment、activeProbe、tentativeCandidates
* retrieved knowledge（検索対象時）

根拠は `service.py:_question_generator_field_context()` 2240-2252、`_question_definition_context()` 2255-2347、`_generate_question_text()` 2789-2818。直近会話の上限は `_QUESTION_GENERATOR_RECENT_MESSAGE_LIMIT = 6`（同ファイル107）。

従って、Fast化で「全State / 全fields / 履歴30件」をQGへ渡さなくなったことは確認できるが、`questionText`やfield descriptionがコード上から削除されたことは確認できない。`基本プロフィール`から「関わった相手」等へ変質した実例は、実行時に実際に送信された `questionDefinition`、provider prompt、responseを保存して初めて判定できる。

Question Generatorは `select_next_question_target()` の後に呼ばれ、selected targetを受け取る（`service.py:1798-1894`, `2669-2905`）。つまりコード上の責務は「何を聞くか」ではなく「選択済みtargetをどう表現するか」に近い。Provider system promptもBackend target/questionDefinitionを仕様とする（`app/api/src/ai_interviewer_api/agents/interview_knowledge/provider.py:618-638`）。

### 8.3 QG streamingの実態

`_generate_question_text()` は callback、flag、provider streamがあり、検索contextが空で、confirmation/candidate targetでない場合にのみ `generate_question_stream()` を選ぶ（`service.py:2820-2885`）。deltaとfirst sentenceのmetricsは記録するが、呼び出し元が `generate_structured_interview_result()` をawaitして最終question textをresultへ組み立てる。

OpenAI coordinatorは `InterviewBridge.process_turn()` の非stream APIを呼ぶ（`coordinator.py:356-365`）。そのためOpenAI経路ではQG deltaをRealtimeへ逐次渡していない。reply text完成後に `_send_backend_reply()` が1回の `response.create`を送る（`coordinator.py:389-458`）。

## 9. Duplicateの根本監査

### 9.1 現在のID変換

```text
OpenAI call_id
  ↓ OpenAIRealtimeSession.call_id
voice_session_id
  ↓ coordinatorが保持
OpenAI conversation item_id
  ↓ clientTurnId = "openai-{item_id}"
VoiceTurn.id
  ↓ APIのturn_id
Frontend message id = "openai-user-{item_id}"
Assistant response id
  ↓ response metadata の kikiori_response_id / response_id
Frontend merge key = voiceResponseId 等
```

根拠:

* OpenAI sidebandは `_processed_transcript_items: set[str]` をsessionメモリに持ち、`item_id`単位でcompleted eventを一度だけ `_process_turn` task化する（`coordinator.py:69`, `201-221`）。
* `_process_turn()` は `client_turn_id=f"openai-{item_id}"` としてBridgeへ渡す（`coordinator.py:356-364`）。
* APIは同一session + clientTurnIdをlock内で検索し、同じtranscript/state versionなら既存VoiceTurnを返す（`voice_interview.py:258-298`）。
* `COMMITTED`のTurnを再処理せず保存済み結果を返す（`voice_interview.py:566-569`）。
* Browser completed eventは `openai-user-{item_id}` をUI keyにして一度だけ追加する（`useRealtimeVoiceInterview.ts:187-216`）。Workspace側は `voiceClientTurnId`、`voiceTurnId`、`voiceResponseId`等でmergeする（`useKnowledgeWorkspaceController.ts:1531-1589`）。

### 9.2 1 -> 2になる可能性がある境界

現行コード上、Browser completedが直接Backendを二重送信する構造は確認できない。しかし、以下は別々の防波堤であり、全体で永続Exactly-onceを保証する単一契約ではない。

| 境界 | 現行防止策 | 残る観測上の弱点 |
|---|---|---|
| Realtime event -> sideband process | in-memory `item_id` set | session/process restartで失われる |
| sideband -> VoiceTurn | `openai-{item_id}` clientTurnId + in-process lock | DB schemaにsource item専用unique keyがない |
| VoiceTurn -> process | lifecycle `COMMITTED` | concurrent process / state conflictはAPIのlock・status依存 |
| Browser final -> UI | finalized key | metadata/keyが欠ける場合のfallback mergeに依存 |
| assistant -> UI | response id map + workspace merge | transport/UIの同一性とDB Turn identityは別管理 |

現時点で実E2Eから「completed event数」「_process_turn task数」「保存行数」「UI追加数」を取得していないため、実際の画面の2重表示がどの境界で1->2になったかはコード監査だけでは確定できない。追加したharnessはAPIの同一client ID再利用を確認するが、Browser event回数の測定は別E2E traceが必要である。

## 10. OpenAIの実際のクリティカルパス

### 10.1 Transcript Final -> First Audio

```text
OpenAI semantic VAD / input audio
  ↓ sideband event: input_audio_buffer.speech_started / speech_stopped
conversation.item.input_audio_transcription.completed
  ↓ coordinator._handle_event():225-272
_process_turn task生成
  ↓ coordinator._process_turn():407-452
startup lifecycleが未完了なら `_pending_first_user_turns` に保持し
`_conversation_ready.wait()` をawait
  ↓ coordinator.py:416-452
_turn_lock取得
  ↓ coordinator.py:477-489
InterviewBridge.process_turn()
  ↓ Bridge.process_turn():116-152
POST /internal/.../turns で save_turn
  ↓ interview_api.py:178-215
POST /internal/.../turns/{turn_id}/process
  ↓ interview_api.py:269-299
_process_voice_turn()
  ↓ voice_interview.py:555-617
TurnをEVALUATINGへ保存、user message保存
  ↓
CONTROLなら `_commit_control_turn()`、それ以外は `_process_structured_voice_turn()`
  ↓ voice_interview.py:591-607

Fast OFF:
  speculative retrieval開始
  ↓ full Structured Interpreter
  ↓ transcript correction / dialogueAct / updates
  ↓ State apply / completion / next target
  ↓ RAG future resolve または正式RAG
  ↓ Question Generator全文完了

Fast ON eligible:
  Background Structured Future submit
  ↓ Fast future.result()をawait
  ↓ provisional next target
  ↓ target用RAG future resolve
  ↓ Question Generator全文完了
  ↓ Backgroundは別Futureで継続

Turn / VoiceSession / assistant message commit
  ↓
reply_ready
  ↓ coordinator._process_turn():487-528
sideband response.create
  ↓ coordinator._send_backend_reply():540-588
response.created
  ↓
response.output_audio.delta
  ↓ coordinator.py:287-289 / Browser DataChannel
remote audio track -> HTMLAudioElement.play()
```

Realtimeには `reply_text` が完成するまで `response.create` を送らない。`response.create`の `input` に `reply_text` を埋め込み、instructionsで内容変更・追加を禁止している（`coordinator.py:430-457`）。`conversation.item.create`はこの経路では送っていない。RealtimeはBackendが決めた文を音声化する役割に近いが、Realtimeモデルが完全逐語であることをBackend側で検証しているわけではない。

### 10.2 主要await一覧

Transcript FinalからOpenAI First Audioまでの主な直列awaitは次のとおり。

1. `coordinator.py:431` — 起動未完了時の `await asyncio.shield(self._conversation_ready.wait())`。初回`response.create`送信までに届いた最初のUser Turnだけが対象。
2. `coordinator.py:477` — `async with self._turn_lock`。初回reply/前Turnがlockを保持していれば待つ。
3. `interview_bridge.py:138` — `await self.save_turn(...)`。VoiceTurn保存の内部HTTP。
4. `interview_bridge.py:149` — `await self.process_saved_turn(...)`。process endpointのHTTP。
5. `voice_interview.py:578-589` — Turn lifecycle保存、record/user message/state snapshot読み込み。
6. Fast ONの場合 `service.py:541` — `fast_future.result()`。Fast providerの完了を待つ（ThreadPool futureだが呼び出し元は同期関数）。
7. Fast ONの場合 `service.py:608-629` -> `_generate_question_text()` — speculative RAG futureの `resolve()`。final queryと一致しない場合は正式Retrievalもこの区間で行う。
8. Full/Fast共通 `service.py:3139`以降 — Question Generator streamまたはnon-stream providerの完了。OpenAI coordinatorではon-delta callbackを渡さないため、最終文字列完了待ちになる。
9. `voice_interview.py:625-904` — State/Turn/assistant messageのcommitとprocess result構築。
10. `coordinator.py:540-588` — `_send_backend_reply()`、さらに `_send_lock`でsideband送信を待つ。
11. OpenAI側の `response.output_audio.delta` 到着 — `coordinator.py:287-289` がfirst audioを記録し、Browser側remote audio trackが再生する。

`background_future`はこのawait chainに含まれない。Fast ONのforegroundはBackground Structured完了をawaitしない。完了後のBackground結果は`apply_background_state_proposal()`へ渡され、保護された会話制御Stateを上書きせず、同じState Writer境界で抽出提案だけを安全にmergeする。

### 10.3 OpenAI create_task一覧

| 作成箇所 | task | 実効性 |
|---|---|---|
| `OpenAIRealtimeSession.start()` 78-94 | `_run_sideband()`、`_expire_after_limit()` | sidebandとexpiryは並行 |
| `_handle_event()` 172-185 | `_send_initial_reply()` | sideband event loopと初回reply処理が別task。ただしTurn側がinitial taskをawaitする |
| `_handle_event()` 201-221 | `_process_turn()` | event loopとTurn処理が並行。Turn内部はlockで直列化 |
| `start_fast_interview_turn()` 423-482 | Background validation Future | Fast providerとBackground providerを重ねる。Backgroundは直後awaitされない |
| `start_fast_interview_turn()` 493-516 | Fast provider Future | `.result()`でFastだけforeground待ち |
| `start_fast_interview_turn()` 601-615 | speculative Retrieval Future | target確定後に作られ、QGがresolveで待つ |
| Transcribe `start()` 237-267 | Transcribe start、state load、endpoint loop | Transcribe startとstate loadは同時開始だが、start完了後state loadをawait |
| Transcribe `_finalize_user_turn()` 729-739 | `_process_interview_turn()` | audio runtimeをblockせず処理開始。ただし内部Bridgeをawait |
| Transcribe `_process_interview_turn()` 801-837 | `_play_streaming_formal_reply()` | API streamを読みながらPollyへdeltaを流す。これはTranscribe経路のみ |
| Nova `ToolTurnCoordinator` 114-166 | `process_interview_bridge_turn()`、forced tool task | transcript/tool条件後にBridgeをtask化。tool結果送信では必要に応じてawait |

`asyncio.create_task()` の存在だけでOpenAIのRAG/QGが並列になっているわけではない。OpenAIはBridgeの非stream processをawaitし、API内でQuestion Generator全文完了までresultを返さず、完了後にRealtimeへ一回だけ送っている。

## 11. Realtimeの責務とイベント

### Browser WebRTC / DataChannel

`openaiRealtimePeerConnection.ts:25-126` がBrowserのRTCPeerConnection、microphone track、remote audio element、DataChannel `oai-events`を管理する。DataChannelで受けたJSONはhookへ渡す。Browserは次を扱う。

* `conversation.item.input_audio_transcription.delta` -> partial transcript UI（`useRealtimeVoiceInterview.ts:178-185`）
* `conversation.item.input_audio_transcription.completed` -> final User UI messageのみ（同:187-216）
* `response.output_audio_transcript.delta` -> assistant streaming UI message（同:231-248）
* `response.output_audio_transcript.done` -> assistant final UI message（同:265-276）
* `response.output_audio.delta` -> speaking状態 / Browser first-audio log（同:250-263）
* `response.created`, `response.done`, cancel/error -> UI status（同:218-323）

Browserはsidebandへ `response.create`を送らない。Backendのreply送信はsidebandのみである。

### app/voice sideband

`coordinator.py:116-285` はsideband WebSocketから同じRealtime Sessionのeventを監視する。

* `session.created` -> `session.update`送信、初回reply task作成
* `input_audio_buffer.speech_started` -> active response時に `response.cancel` と `output_audio_buffer.clear`
* `speech_stopped` -> speech end metric
* `conversation.item.input_audio_transcription.completed` -> dedupe、`_process_turn` task
* `response.created` / `response.output_audio.delta` / done -> server-side timeline/usage
* `_send_backend_reply()` -> `response.create`

同じRealtime eventをBrowserとsidebandがそれぞれ受けるが、Browser側はUI、sideband側はBackend processという責務分離である。

## 12. Disconnect / session寿命の現在実装

今回の現行アルゴリズム監査では、次のcleanup経路も会話制御の境界として確認した。

* Browser hookの `stop()` は `peerRef.current?.stop()` と `DELETE /voice/webrtc/{id}` を呼ぶ（`useRealtimeVoiceInterview.ts:115-130`、API client `realtimeVoiceClient.ts:128-139`）。
* component cleanupは `stop("component_unmounted")`、beforeunloadは `deleteVoicePeerConnection(...,"browser_unload",...)`（`useRealtimeVoiceInterview.ts:707-721`）。
* start失敗時もpeer停止後にDELETEする（同:677-701）。
* DELETE endpointは `coordinator.close(reason)`、registry removal、peer closeを実行する（`app/voice/src/ai_interviewer_voice/routers/webrtc.py:192-204`）。
* OpenAI session `close()` はturn/initial/run/expiry taskをcancelし、`_finish()` がhangup、usage persistence、registry callbackを行う（`coordinator.py:100-114, 481-540`）。
* Interview APIエラー時、OpenAI `_process_turn()` は `close(reason="interview_api_failed")` または `interview_processing_failed` を呼ぶ（`coordinator.py:366-384`）。

従って、マイクtrack停止、Browser cleanup、voice DELETE、sideband close、OpenAI hangupは同じではない。Browser hookのunmount/ページ離脱はSession終了まで連鎖するが、通常のRealtime transcript completedだけではcleanupしない。Backend processing中にSessionを維持する設計上の入口は存在する一方、Interview API例外、明示stop、component unmount、expiryでは終了する。

## 13. 現在の設定値（コード既定値）

Secretや実環境の値は記載しない。以下は設定コードのdefaultである。

| 設定 | 現行値 / default | 根拠 |
|---|---|---|
| `STRUCTURED_INTERVIEW_FAST_PATH_ENABLED` | `false` | `app/api/.../core/config.py:72-75` |
| Fast model | `global.openai.gpt-5.6-luna` | 同:76-79 |
| Fast reasoning | `none` | 同:80-83 |
| Fast max output | `160` | 同:84-86 |
| Structured reasoning | `low` | 同:93-96 |
| QG reasoning | `none` | 同:97-100 |
| Structured medium retry | `medium` | 同:101-104 |
| Structured max output | `6000` | 同:105-107 |
| QG max output | `600` | 同:108-110 |
| QG streaming | `true` | 同:111-114 |
| OpenAI Realtime model | `gpt-realtime-2` | `app/voice/.../openai_realtime/config.py:39-50` |
| OpenAI turn detection | `semantic_vad`, eagerness `low`, create_response `false`, interrupt_response `false` | `config.py:80-122` |
| OpenAI output | `audio`、`parallel_tool_calls=false` | `config.py:80-122`, `coordinator.py:430-457` |
| common voice default provider | Backend voice config `VOICE_RUNTIME_PROVIDER` は `transcribe_polly`、Frontend fallbackも同じ | `app/voice/src/ai_interviewer_voice/config.py:10-16`、`app/web/src/features/realtime-voice/api/realtimeVoiceClient.ts:4-6` |

## 14. Characterization Testの使い方

現在のprovider-independent testは、Text/音声transportを呼ばず、固定されたrecord/knowledge/fields/stateに対して既存Structured serviceを観測する。

```bash
cd app/api
UV_CACHE_DIR=/tmp/ai-interviewer-test-cache uv run pytest \
  tests/characterization/test_conversation_algorithm_characterization.py -q
```

追加したharnessで、同じStateに対して次のJSONを比較できる。

* 正常回答: `structuredDialogueAct=ANSWER`、`nextTarget=field-work`
* Confirmation: `stateBefore.fieldStates.field-profile.answerState=AWAITING_CONFIRMATION` -> `stateAfter...=CONFIRMED`
* Clarification / rejection / hesitation / correction: `action`、current target、State projection
* Duplicate identity: source item `realtime-item-001` -> `clientTurnId=openai-realtime-item-001` -> stored VoiceTurn 1件

今後Fast ON/OFFを比較する場合は、production settingをテスト全体へ漏らさず、Fast providerを注入できるcharacterization caseを追加する必要がある。現行harnessはFull Structuredを観測するものであり、Fast ONのThreadPoolタイミングそのものを模擬してはいない。

## 15. Canonical Algorithm（Phase 1/2実装状況）

現行コードとのGapを確認した上で定義した順序のうち、Canonical Intent/Action、Single State Writer、source ID付きVoice TurnのDurable Dedup、Canonical Question Definitionの不変契約を実装した。Initial Question全面統合と出力Streamingはまだ対象外である。

```text
Provider raw input
  ↓
Canonical User Turn Creation
  - provider source idを保存
  - voice_session_id + source idからcanonical_turn_idを決める
  ↓
Turn Deduplication
  - `voice_session_id + clientTurnId`をAPI repositoryとDB partial unique indexで保護
  - 重複POSTは既存VoiceTurnを返し、processing claimも条件付き更新で一度だけ取得
  - OpenAI/Nova/Transcribeはprovider source IDを`clientTurnId`へ接続。TranscribeはResultIdのないストリームだけ再接続時のsource identityを復元できない
  ↓
One Conversation Interpretation
  - 既存 StructuredDialogueAct を正規のintent型として再利用
  - ANSWER / QUESTION_TO_ASSISTANT / CLARIFICATION_REQUEST /
    CONFIRMATION / REJECTION / CORRECTION / HESITATION等
  ↓
Backend Conversation Policy
  - `services/conversation_policy.py` がintentをCanonical Actionへ一度だけ変換
  - PROCESS_ANSWER / EXPLAIN_CURRENT_QUESTION /
    CONFIRM_PENDING_CANDIDATE / REJECT_PENDING_CANDIDATE /
    APPLY_CORRECTION / WAIT_FOR_USER / REPEAT_CURRENT_QUESTION等
  ↓
Action-specific Processing
  - ANSWERだけFast Answer Checkを許可
  - 非ANSWERはFast Answer Checkへ入れない
  ↓
One State Transition
  - `services/interview_state_transition.py:commit_interview_state()`だけが永続Stateをcommit
  - Background検証はsource turn/state version付きproposalとしてreconciliation
  ↓
One Next Target Selection
  - coordinatorがtarget definitionまで確定
  ↓
Question Rendering
  - canonical question / description / required itemsを保持
  - LLMは表現だけを担当
  ↓
Provider Output
```

### 15.1 Router型について

既存 `StructuredDialogueAct` を新しいenumなしでCanonical User Intentとして再利用している（`schemas.py:27-39`、`services/conversation_policy.py:21-188`）。`VoiceTurnIntentOutput` の粗い `ANSWER` / `CONTROL` はtransport境界に残るが、BackendのCanonical Action決定には使わない。

### 15.2 Fastの位置

```text
Canonical dialogueAct
  ├─ ANSWER -> Fast Answer Check -> provisional advance
  ├─ QUESTION_TO_ASSISTANT / CLARIFICATION_REQUEST -> explain current definition
  ├─ CONFIRMATION -> confirm pending candidate
  ├─ REJECTION -> reject pending candidate
  ├─ CORRECTION -> apply/stage correction
  └─ HESITATION / CONTROL / OTHER -> wait or current target
```

Fast schemaに会話制御値を無制限に追加して第二のStructured Interpreterを作らない。分類を一度行い、その分類に応じてFastが呼ばれる構造にする。

### 15.3 Initial Question

初回のCanonical出力は`create_voice_session()`で作成・保存し、OpenAIのRealtime `session.created`後にだけ送信する。FrontendはVoice Session作成レスポンスから先に表示し、sidebandは初回`response.create`送信完了を`READY_FOR_USER_TURN`の条件とする。通常Turnと完全に同じState transitionへ統合することは次Phaseの対象だが、初回出力と最初のUser Turnのraceは起動ゲートで防ぐ。

## 16. Gap Analysis

| Requirement | Current implementation | Problem | Severity | Affected providers | Proposed owner |
|---|---|---|---|---|---|
| 1 Turn -> 1 canonical identity | OpenAI/Nova/Transcribeのsource ID、API repository、DB partial unique index、UI identity key | ResultIdを提供しないTranscribe streamと、client IDを生成しない外部/旧callerはDurable dedup対象外 | Medium | Voice全体、特に旧Transcribe stream | `app/api` Canonical Turn repository + provider adapter |
| 1 intentの正本 | BackendのCanonical PolicyをPhase 1で追加。Provider側には粗いtransport値が残る | transport `turnType` はまだProvider境界に存在し、全ProviderのCanonical Turn adapter統合は未完了 | Medium | Voice全体 | app/api Conversation Policy + Turn adapter |
| Stateを1 writerへ集約 | `commit_interview_state()` とversion付きBackground mergeをPhase 2で追加 | processを跨ぐState version compare/commitの原子性はDB側で追加検討が必要 | Medium | Voice Fast ON | app/api versioned reconciliation |
| Confirmationの単一遷移 | Canonical Action -> existing `_confirm_target()` | Initial経路と全Providerのsource ID adapterはまだ別境界 | Medium | Voice全体 | Canonical Action |
| clarificationの単一経路 | Canonical Action -> existing help / `_keep_current_question()` | 実LLM RouterのQTA/Clarification境界は観測継続 | Medium | Voice全体 | Canonical dialogueAct + Action |
| Initial questionの共通Policy | `create_voice_session()`が`initialReplyText`を作成し、OpenAIはstartup gate後に送信。FrontendはSession作成直後に表示 | 初回Actionは通常TurnのCanonical Actionとはまだ別経路 | Medium | Voice全体 | app/api Policy + renderer |
| Provider非依存Turn contract | OpenAI/Nova/Transcribe runtimeはsource IDを`clientTurnId`へ接続。Textはvoice contract外 | Text/旧callerのsource ID adapterは別境界 | Medium | 全Provider | adapter -> Canonical Turn |
| QGがdefinitionを厳守 | `questionDefinition`、hash、`questionProgress`を分離し、Promptにも定義を渡す | LLM出力が定義から逸脱した場合の自動validatorは未実装 | Medium | Text/Voice | backend renderer/validator |
| QG全文待ちを避ける | Internal streamはあるがOpenAIはnon-stream Bridge | OpenAI First Audioはreply全文後 | High | OpenAI | later streaming boundary |
| Background resultのState整合 | proposal + base/current version比較 + safe mergeを追加。control-state fieldsは復元保護 | Cross-processでのState commit/version compareは残課題 | Medium | Voice Fast ON | same turn/version reconciliation |
| `CONTROL`のdialogueAct | API 2値分類後 `_commit_control_turn()` | clarification/rejection等と異なる制御語彙 | Medium | Text/Voice caller dependent | Canonical Action |
| UIとBackendのID統合 | frontend merge keyとVoiceTurn client ID | UI duplicateとBackend duplicateを同一契約で追えない | Medium | 全Voice | Canonical IDs |
| Text SSEの文分割 | newline + artificial delay | 音声ではないが出力境界がProviderごとに異なる | Low | Text | output adapter |

## 17. 実装計画

依存関係順は次のとおり。

1. characterization / State Traceを拡張し、全Caseの入力、State、output、IDsをfixture化する（実施済み）。
2. 実LLM Routerを100ケースで評価し、Expected / Router / Full Structuredを比較する（Phase 1.5実施済み）。
3. 既存 `StructuredDialogueAct` をCanonical Intentとして再利用し、Canonical Action mappingを追加する（Phase 1実施済み）。
4. State transitionとnext target selectionをAction単位で一度だけ実行する（Phase 1/2実施済み）。
5. Background Structuredをproposal化し、base/current state version付きでCoordinatorから安全にmergeする（Phase 2実施済み）。
6. Initial Questionを同じQuestion Renderer/Policy契約へ接続する（次Phase）。
7. Provider source IDからCanonical User Turn IDを生成し、API側でdurable dedupeする（実施済み。ResultIdを提供しない旧Transcribe streamは安定IDなし）。
8. Question definitionをimmutableなtarget contractとして分離し、canonical questionがある場合のrender規則を固定する（実施済み）。
9. Provider adapterを通じてText/Transcribe/Nova/OpenAIの入力境界をCanonical Turnへ揃える（Voiceのsource ID伝播を実施。Textは別境界）。
10. 最後にBrowser/sideband/Polly/NovaのE2Eで、同じCanonical Turn IDとActionを追跡する（次Phase）。

この順番が終わるまで、`needs_question_explanation`等を個別に増やしてFastを第二のdialogue classifierにしない。

## 18. 調査上の未確定事項

次は今回のPhaseの対象外で、実行traceまたは次Phaseが必要である。

* 実Browserで取得した1発話のOpenAI `completed` event回数と、実DB上の行数の1:1対応（コード側のカウンタ・ログ・テストは追加済みだが、今回の検証ではlive E2Eを実行していない）。
* 実BrowserでのBrowser final callback回数、sideband `_process_turn` task回数、API保存行数、UI message追加回数の1:1対応。
* Case Aで「大丈夫です。」が同じtargetへ戻る実行時の `stateBefore/stateAfter/stateVersion`。
* Question Generatorへ実際に渡ったserialized promptと、LLMが返した質問の逸脱分類。
* Fast ON時の各dialogueActに対する実際のFast provider outputとBackground outputの一致率（Fastの回答十分性とRouter Intentは別契約のため、別評価が必要）。

### 18.1 Phase 1.5 実LLM Router評価

`app/api/tests/evaluation/evaluate_canonical_intent_router.py` を使い、100件の日本語ケースを本番相当のRouter modelへ送り、結果を `/tmp` のJSONへ保存した。入力・出力全文や認証情報は保存・表示していない。

同じデータセットで観測した値は次のとおり（LLMの実行ごとの揺らぎがあるため、Router単独実行とStructured比較実行を併記する）。

| 指標 | Router単独 | Structured比較実行 |
|---|---:|---:|
| ケース数 | 100 | 100 |
| 正解率 | 97% | 96% |
| 非ANSWER -> ANSWER | 0/90 (0%) | 0/90 (0%) |
| Router latency median / p90 / max | 801.2 / 1476.1 / 5030.7 ms | 818.9 / 2201.7 / 18391.4 ms |
| Router input / output tokens median | 1058 / 21 | 1058 / 21 |
| Full Structured正解率 | — | 88% |

Structured比較実行のRouter分類は、`CONFIRMATION`、`REJECTION`、`CORRECTION`、`HESITATION`、`BACKCHANNEL`でrecall 100%。`CLARIFICATION_REQUEST`はprecision 83.3% / recall 100%、`QUESTION_TO_ASSISTANT`はprecision 100% / recall 70%だった。ただし両者は同じ `EXPLAIN_CURRENT_QUESTION` Actionへ入るため、Action上の非ANSWER誤分類は0件だった。

この結果をPhase 1.5のAcceptanceとして、Phase 2のState Writer一本化へ進めた。実行結果JSONはリポジトリへ追加していない。
* `VoiceSession`の実環境provider default値。コードfactoryの対応とenv実値は別なので、secretを含まない設定ダンプが必要。

これらは推測で埋めず、次のE2E/structured loggingで同一 `voice_session_id`、Canonical Turn ID、OpenAI item idを出力して確認する。

## 19. 参照した主要コード

* `app/api/src/ai_interviewer_api/services/voice_interview.py`
* `app/api/src/ai_interviewer_api/services/conversation_policy.py`
* `app/api/src/ai_interviewer_api/services/interview_state_transition.py`
* `app/api/src/ai_interviewer_api/agents/interview_knowledge/service.py`
* `app/api/src/ai_interviewer_api/agents/interview_knowledge/coordinator.py`
* `app/api/src/ai_interviewer_api/agents/interview_knowledge/schemas.py`
* `app/api/src/ai_interviewer_api/agents/interview_knowledge/fast_interpreter/schemas.py`
* `app/api/src/ai_interviewer_api/core/config.py`
* `app/voice/src/ai_interviewer_voice/services/interview_bridge.py`
* `app/voice/src/ai_interviewer_voice/clients/interview_api.py`
* `app/voice/src/ai_interviewer_voice/runtimes/openai_realtime/coordinator.py`
* `app/voice/src/ai_interviewer_voice/runtimes/openai_realtime/client.py`
* `app/voice/src/ai_interviewer_voice/routers/webrtc.py`
* `app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/runtime.py`
* `app/voice/src/ai_interviewer_voice/runtimes/nova_sonic/tool_turn_coordinator.py`
* `app/api/tests/evaluation/canonical_intent_cases.py`
* `app/api/tests/evaluation/evaluate_canonical_intent_router.py`
* `app/api/tests/services/test_interview_state_transition.py`
* `app/voice/src/ai_interviewer_voice/services/runtime_factory.py`
* `app/web/src/features/realtime-voice/hooks/useRealtimeVoiceInterview.ts`
* `app/web/src/features/realtime-voice/webrtc/openaiRealtimePeerConnection.ts`
* `app/web/src/features/realtime-voice/api/realtimeVoiceClient.ts`
* `app/web/src/routes/useKnowledgeWorkspaceController.ts`
* `app/api/tests/characterization/conversation_algorithm_harness.py`
* `app/api/tests/characterization/test_conversation_algorithm_characterization.py`
