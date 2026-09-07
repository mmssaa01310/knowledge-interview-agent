from __future__ import annotations

import re
from dataclasses import dataclass

_STRONG_BOUNDARIES = frozenset("。？！!?\n")


@dataclass(frozen=True)
class PollyTextChunkerConfig:
    first_min_chars: int = 10
    first_max_chars: int = 30
    following_min_chars: int = 20
    following_max_chars: int = 80


class PollyTextChunker:
    """LLM deltaにも完成済みtextにも使える増分テキスト分割器。"""

    def __init__(self, config: PollyTextChunkerConfig | None = None) -> None:
        self._config = config or PollyTextChunkerConfig()
        self._buffer = ""
        self._chunk_index = 0

    def feed(self, text: str, *, final: bool = False) -> list[str]:
        self._buffer += text
        chunks: list[str] = []
        while True:
            boundary = self._next_boundary(final=final)
            if boundary is None:
                break
            chunk = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if chunk:
                chunks.append(chunk)
                self._chunk_index += 1
        return chunks

    def push(self, text: str, *, final: bool = False) -> list[str]:
        """Alias used by streaming callers to make delta ingestion explicit."""

        return self.feed(text, final=final)

    def _next_boundary(self, *, final: bool) -> int | None:
        if not self._buffer:
            return None
        min_chars, max_chars = self._limits()

        comma = self._buffer.find("、")
        comma_min_chars = min(min_chars, 10)
        strong = _first_strong_boundary(self._buffer)
        if (
            comma >= 0
            and comma + 1 >= comma_min_chars
            and strong is not None
            and strong - (comma + 1) >= comma_min_chars
        ):
            return comma + 1

        if strong is not None and (strong >= min_chars or final):
            return strong

        if comma >= 0 and comma + 1 >= min_chars:
            return comma + 1

        if len(self._buffer) >= max_chars:
            natural = _last_natural_boundary(self._buffer, max_chars, min_chars)
            return natural or max_chars

        if final:
            return len(self._buffer)
        return None

    def _limits(self) -> tuple[int, int]:
        if self._chunk_index == 0:
            return self._config.first_min_chars, self._config.first_max_chars
        return self._config.following_min_chars, self._config.following_max_chars


def split_text_for_polly(text: str, config: PollyTextChunkerConfig | None = None) -> list[str]:
    return PollyTextChunker(config).feed(text, final=True)


def _first_boundary(text: str, boundaries: frozenset[str]) -> int | None:
    positions = [text.find(item) for item in boundaries if item in text]
    return min(positions) + 1 if positions else None


def _last_natural_boundary(text: str, limit: int, minimum: int) -> int | None:
    candidate = text[:limit]
    positions = [
        position
        for position in (
            _last_strong_boundary(candidate),
            candidate.rfind("、") + 1,
        )
        if position >= minimum
    ]
    return max(positions) if positions else None


def _first_strong_boundary(text: str) -> int | None:
    positions = [
        index + 1
        for index, mark in enumerate(text)
        if mark in _STRONG_BOUNDARIES
        and (mark != "." or _safe_ascii_period(text, index))
    ]
    # ASCII full stops are not in _STRONG_BOUNDARIES so they are considered
    # separately.  This keeps decimal values, URLs, and abbreviations intact.
    positions.extend(
        index + 1
        for index, mark in enumerate(text)
        if mark == "." and _safe_ascii_period(text, index)
    )
    return min(positions) if positions else None


def _last_strong_boundary(text: str) -> int:
    positions = [
        index + 1
        for index, mark in enumerate(text)
        if mark in _STRONG_BOUNDARIES
    ]
    positions.extend(
        index + 1
        for index, mark in enumerate(text)
        if mark == "." and _safe_ascii_period(text, index)
    )
    return max(positions, default=0)


def _safe_ascii_period(text: str, index: int) -> bool:
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    # A delta may end immediately after the period in a decimal value such
    # as ``3.14``.  Wait for the next delta to reveal whether this is a safe
    # sentence boundary; ``final=True`` still flushes the remaining buffer.
    if not following:
        return False
    if previous.isdigit() and following.isdigit():
        return False
    if following and not following.isspace():
        return False
    prefix_match = re.search(r"([A-Za-z]{1,8})$", text[:index])
    prefix = prefix_match.group(1) if prefix_match else ""
    if len(prefix) <= 2 and prefix:
        return False
    if prefix.casefold() in {"www", "http", "https"}:
        return False
    return bool(previous and (previous.isalnum() or previous in "）)】]"))
