from __future__ import annotations

import os

from ai_interviewer_api.agents.interview.schemas import InterviewTurnInput
from ai_interviewer_api.agents.interview.service import run_interview_turn
from ai_interviewer_api.core.config import settings


def main() -> int:
    if os.getenv("RUN_STRANDS_SMOKE") != "1":
        print("Set RUN_STRANDS_SMOKE=1 to run the Strands interview agent smoke test.")
        return 0

    result = run_interview_turn(
        InterviewTurnInput(
            knowledge_id="smoke-knowledge",
            user_message="センサーエラーが出たときは最初に接点を確認します。",
        )
    )
    print(f"model_id={settings.bedrock_model_id}")
    print(f"region={settings.bedrock_aws_region}")
    print(result.model_dump_json(indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
