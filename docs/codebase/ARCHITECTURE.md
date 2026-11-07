# アーキテクチャ

## 1. 方式

AIインタビューはReact/ViteのWeb、FastAPI API、PostgreSQLからなるモノレポである。APIは`routers → services/agents → repositories`で責務を分ける。OpenAI APIとの通信はBackendに集約する。

## 2. テキストインタビュー

```text
Browser → Web feature → /api → FastAPI router
  → service / agent → OpenAI Responses API
  → Structured Output検証 → Repository → PostgreSQL
```

## 3. 音声インタビュー

```text
Browser WebRTC ↔ OpenAI GPT Live
Backend /api/live → OpenAI Responses API delegation
Backend → 認可・状態検証・PostgreSQL保存
```

OpenAI Project APIキーは設定画面から登録し、AES-GCMで暗号化してBackendのDBに保存する。BrowserにはサーバーAPIキーを渡さない。GPT Liveの会話、意味評価の委譲、状態保存の詳細は[音声アーキテクチャ](../architecture/voice/realtime-voice.md)を参照する。

## 4. モジュール責務

| モジュール | 責務 |
| --- | --- |
| `app/web` | UI、i18n、APIクライアント、GPT Live WebRTC |
| `app/api/.../routers` | HTTP境界、認証・認可 |
| `app/api/.../services` | 業務処理、状態遷移、検索 |
| `app/api/.../agents` | Responses APIアダプターとAI出力の検証 |
| `app/api/.../repositories` | PostgreSQL Store |
| `app/worker` | 文書取り込み状態の最小サンプル |

## 根拠

- `app/api/src/ai_interviewer_api/main.py`
- `app/api/src/ai_interviewer_api/routers/routes.py`
- `app/api/src/ai_interviewer_api/agents/interview_knowledge/provider.py`
- `app/api/src/ai_interviewer_api/services/live_session.py`
- `infra/docker-compose.yml`
