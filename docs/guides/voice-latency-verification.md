# GPT Live音声のレイテンシ確認

GPT Live音声ではBrowserのWebRTCイベントとAPIの状態更新ログを記録し、同一Session・Turn IDで時系列を確認する。

主に確認するイベントは次のとおり。

- `gpt_live_session_created`
- `gpt_live_response_started`
- `gpt_live_first_audio`
- `gpt_live_input_transcript_final`
- `gpt_live_state_update_prepared`
- `gpt_live_state_persisted`
- `gpt_live_response_done`

音声入力終了からTranscript確定、最初の応答音声、State保存までの差分を計算する。Responses Delegationの完了やDataChannel ACKは、Liveのユーザー向け音声開始と同一視しない。

通常のE2EではFake WebRTC/DataChannelを使う。実ネットワークの測定は、開発者が明示的にLevel 3テストを起動した場合だけ行う。出力レポートやログにAPIキー、セッショントークン、会話全文を保存しない。
