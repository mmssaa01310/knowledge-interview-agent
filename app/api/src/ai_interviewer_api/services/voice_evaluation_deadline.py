"""
Role:
    音声回答評価のハードデッドラインと遅延結果破棄を管理する。

Summary:
    外部モデル評価を専用executorで実行し、期限内の結果だけを呼び出し元へ返す。
    期限後に完了した結果は状態更新へ渡さず、監査ログへ記録する。

Relations:
    Used by services.voice_interview. Independent from interview state persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar


logger = logging.getLogger(__name__)

T = TypeVar("T")
_EVALUATION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voice-evaluation")


@dataclass(frozen=True)
class VoiceEvaluationRequest:
    voice_session_id: str
    voice_turn_id: str
    question_id: str | None
    field_id: str
    evaluation_request_id: str
    state_version: int
    deadline_at: float


class VoiceEvaluationDeadlineExceeded(TimeoutError):
    pass


def run_with_evaluation_deadline(
    callback: Callable[[], T],
    *,
    request: VoiceEvaluationRequest,
) -> T:
    future = _EVALUATION_EXECUTOR.submit(callback)
    remaining_seconds = max(0.0, request.deadline_at - monotonic())
    try:
        return future.result(timeout=remaining_seconds)
    except FutureTimeoutError as exc:
        if not future.cancel():
            future.add_done_callback(lambda completed: _log_late_result(completed, request))
        raise VoiceEvaluationDeadlineExceeded(request.evaluation_request_id) from exc


def _log_late_result(future: Future[object], request: VoiceEvaluationRequest) -> None:
    outcome = "failed" if future.exception() is not None else "completed"
    logger.warning(
        "late_evaluation_result_discarded voice_session_id=%s voice_turn_id=%s question_id=%s field_id=%s evaluation_request_id=%s state_version=%s deadline_at_ms=%s outcome=%s monotonic_timestamp_ms=%s",
        request.voice_session_id,
        request.voice_turn_id,
        request.question_id,
        request.field_id,
        request.evaluation_request_id,
        request.state_version,
        round(request.deadline_at * 1000),
        outcome,
        round(monotonic() * 1000),
    )
