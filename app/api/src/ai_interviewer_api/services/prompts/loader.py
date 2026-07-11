from __future__ import annotations

from functools import lru_cache
from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent


def _read_prompt(relative_path: str) -> str:
    return (PROMPTS_DIR / relative_path).read_text(encoding="utf-8").strip()


@lru_cache(maxsize=None)
def get_field_fill_system_prompt() -> str:
    return "\n".join(
        [
            _read_prompt("field_fill/base.md"),
            _read_prompt("field_fill/guard.md"),
        ]
    ).strip()


def build_field_fill_system_prompt(custom_prompt: str | None) -> str:
    base_prompt = get_field_fill_system_prompt()
    normalized_custom_prompt = custom_prompt.strip() if custom_prompt else ""
    if not normalized_custom_prompt:
        return base_prompt
    return f"{base_prompt}\n\n{normalized_custom_prompt}"


@lru_cache(maxsize=None)
def get_json_repair_system_prompt() -> str:
    return _read_prompt("field_fill/json_repair.md")


@lru_cache(maxsize=None)
def get_interview_base_system_prompt() -> str:
    return _read_prompt("interview/base.md")


def build_interview_system_prompt(custom_prompt: str | None) -> str:
    base_prompt = get_interview_base_system_prompt()
    normalized_custom_prompt = custom_prompt.strip() if custom_prompt else ""
    if not normalized_custom_prompt:
        return base_prompt
    return f"{base_prompt}\n\n{normalized_custom_prompt}"
