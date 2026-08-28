from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=1)
def load_question_design_prompt() -> str:
    return (_PROMPTS_DIR / "base.md").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def load_question_design_validation_prompt() -> str:
    return (_PROMPTS_DIR / "validation.md").read_text(encoding="utf-8").strip()
