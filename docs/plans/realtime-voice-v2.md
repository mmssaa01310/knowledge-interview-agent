# リアルタイム音声インタビュー v2 計画

Status: Draft  
Target: v2  
Last Updated: 2026-07-14  

関連ドキュメント:

* `docs/spec.md`
* `docs/architecture/voice/realtime-voice.md`

## 1. 目的

この文書は、リアルタイム音声インタビューv1で対象外とした機能を、v2以降の検討対象として整理するための計画である。

`docs/plans/`配下の文書は実装計画であり、確定仕様ではない。実装前には`docs/spec.md`および`docs/architecture/voice/realtime-voice.md`との整合を確認する。

## 2. v2以降の検討対象

以下はv1では実装せず、v2以降で検討する。

* Transcribe + Polly Runtimeの実動作
* Providerの自動fallback
* NovaからTranscribe + Pollyへの無停止切り替え
* DynamoDBによるVoice Session lease
* 複数Voice Gateway間のセッション移送
* ECSタスク障害時の自動引き継ぎ
* 長時間音声ファイル保存
* 複数話者インタビュー
* リアルタイム話者分離
* KVSシグナリングチャネル
* 高度なノイズ抑制
* Voice Session分析ダッシュボード

## 3. 実装前提

v2機能を実装する場合も、以下のv1アーキテクチャ原則は維持する。

* インタビューの意味処理は`app/api`を正本とする。
* `app/voice`から`app/api`のPythonモジュールを直接importしない。
* 音声専用のInterview Agent、RAG、回答評価を追加しない。
* Provider固有型を`app/web`へ露出しない。
* `app/api`が決定したAssistantReplyに対応する音声だけをユーザーへ再生する。
* AI提案は人の承認なしに正式ナレッジ化しない。

## 4. 優先度の考え方

v2の着手順は、v1運用後の実測結果をもとに決める。

優先判断に使う観点は以下。

* Nova Sonicの日本語認識精度
* `reply_text`と実際の発話内容の一致性
* WebRTC接続安定性
* ECSタスク障害時の影響
* 長時間セッションの切断頻度
* 音声品質、遅延、割り込み応答性
* 運用監視で必要なメトリクス
