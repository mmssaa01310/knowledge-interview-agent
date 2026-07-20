from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = """あなたは日本語の音声インタビュアーです。

すべての発話を日本語で行ってください。
英語を追加しないでください。
完了したユーザー発話ごとに process_interview_turn tool を呼び出してください。
tool result を受け取るまで沈黙してください。
tool result の reply_text だけを発話してください。
reply_text を翻訳、言い換え、要約しないでください。
挨拶、了承、説明、追加質問を加えないでください。"""


@dataclass(frozen=True)
class NovaSonicRuntimeConfig:
    provider_name: str = "nova_sonic"
    aws_region: str = "ap-northeast-1"
    model_id: str = "amazon.nova-2-sonic-v1:0"
    voice_id: str = "matthew"
    invoke_timeout_seconds: float = 10.0
    await_output_timeout_seconds: float = 10.0
    endpointing_sensitivity: str = "HIGH"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    enable_forced_tool_use: bool = False
    forced_tool_name: str = "process_interview_turn"
    forced_tool_result_delay_ms: int = 0
    normal_turn_tool_result_target_ms: int = 300
    normal_turn_tool_result_budget_ms: int = 400
    reply_completion_start_timeout_seconds: float = 2.0
    initial_followup_gap_ms: int = 400
    initial_tool_control_text: str = (
        "Call process_interview_turn now to start the interview. "
        "Do not speak before receiving the tool result."
    )
    forced_tool_result_reply_text: str = "ありがとうございます。通常どのような状況で発生するか教えてください。"
    interview_timeout_reply_text: str = "処理に時間がかかっています。もう一度お願いします。"
    interview_error_reply_text: str = "処理に失敗しました。もう一度お願いします。"
    interview_unauthorized_reply_text: str = "認証を確認できませんでした。セッションを終了します。"

    def __post_init__(self) -> None:
        if not self.voice_id.strip():
            raise ValueError("NOVA_SONIC_VOICE_ID must not be empty")
