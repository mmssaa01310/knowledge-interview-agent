from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=1)
def load_learning_support_analysis_prompt() -> str:
    return (_PROMPTS_DIR / "overall_analysis.md").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def load_learning_support_personal_advice_prompt() -> str:
    return (_PROMPTS_DIR / "personal_advice.md").read_text(encoding="utf-8").strip()

