# リアルタイム音声インタビュー

音声インタビューはOpenAI GPT LiveのWebRTC経路に固定する。アプリ内の音声ゲートウェイや別の音声認識・音声合成Providerは置かない。

## 責務

| 部分 | 責務 |
|---|---|
| Browser | マイク入力、WebRTC接続、Live音声の再生、字幕の表示・送信 |
| API | OpenAI APIキーの取得、Live Session作成、Record認可、字幕受付、状態更新の検証・保存 |
| GPT Live (`gpt-live-1`) | 音声認識、自然な音声会話、質問進行、応答音声 |
| Responses API | 必要な場合の構造化意味評価。Live音声会話の別インタビュアーにはしない |

```mermaid
sequenceDiagram
    participant UI as Browser
    participant API as FastAPI
    participant DB as PostgreSQL
    participant OAI as OpenAI GPT Live
    UI->>API: POST /api/live/sessions (WebRTC SDP)
    API->>DB: OpenAI APIキーを復号
    API->>OAI: Live Session作成
    OAI-->>API: SDP answer
    API-->>UI: SDP answer
    UI<->>OAI: WebRTC音声・DataChannelイベント
    UI->>API: Transcript fragments / State Update
    API->>DB: 検証済みInterview Stateを保存
```

## APIキー

アプリ全体設定管理者が設定画面でOpenAI Project APIキーを登録する。APIはキーをAES-GCMで暗号化し、システムスコープのPostgreSQL設定として保存する。暗号鍵は`OPENAI_SETTINGS_ENCRYPTION_KEY`から取得し、Browserには生キーを返さない。テキスト生成、質問設計、GPT Live Session作成で同じキーを利用する。

キー接続確認は`gpt-6-luna`、`gpt-6.1-terra`、`gpt-live-1`へのアクセスを確認する。Live Sessionは作成せず、失敗時は保存済みキーを維持する。

## 状態とTranscript

OpenAI Live Sessionは`POST /api/live/sessions`で作成する。Browserは音声をMediaTrack、イベントとFunction Tool結果をDataChannelで送る。確定Transcriptは`POST /api/live/captures`で受付け、API側のConsumerがResponses Delegationの評価結果を検証して保存する。現在の状態は`GET /api/live/captures/{record_id}`で再取得できる。Live Function Toolからの明示更新は`POST /api/live/state-updates`で検証する。

partial transcriptは確定回答に使わない。認可、質問対象、Evidence、State version、完了状態はBackendが検証する。GPT Liveは質問進行を担当し、Backendは状態の正本とする。ページ離脱や一時的なAPI失敗が起きても、受付済みのTranscript Captureは再試行可能とする。

## 実装箇所

- Browser: `app/web/src/features/realtime-voice/hooks/useGPTLiveVoiceConversation.ts`
- WebRTC: `app/web/src/features/realtime-voice/webrtc/gptLivePeerConnection.ts`
- Session API: `app/api/src/ai_interviewer_api/services/live_session.py`
- Capture API: `app/api/src/ai_interviewer_api/services/live_capture.py`、`live_capture_consumer.py`
- 状態評価: `app/api/src/ai_interviewer_api/services/live_interview.py`
- 共通APIキー: `app/api/src/ai_interviewer_api/services/application_settings.py`
