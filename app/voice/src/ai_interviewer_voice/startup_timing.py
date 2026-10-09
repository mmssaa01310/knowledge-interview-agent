"""共通の音声起動レイテンシ計測ログ。"""

from __future__ import annotations

import json
import logging
from time import monotonic, time
from typing import Any


def log_voice_startup_stage(
    logger: logging.Logger,
    *,
    voice_session_id: str | None,
    provider: str,
    stage: str,
    **details: Any,
) -> None:
    """同一形式でサーバー側の起動区間を記録する。

    monotonic_ms は同一プロセス内の経過時間計算用、timestamp_ms は
    ブラウザ/API/voice のログを相関させるための wall clock である。
    """

    logger.info(
        "voice_startup_stage source=server voice_session_id=%s provider=%s stage=%s "
        "monotonic_ms=%s timestamp_ms=%s details=%s",
        voice_session_id,
        provider,
        stage,
        round(monotonic() * 1000, 3),
        round(time() * 1000),
        json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
