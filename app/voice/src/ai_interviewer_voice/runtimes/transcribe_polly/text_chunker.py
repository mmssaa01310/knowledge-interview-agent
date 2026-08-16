from __future__ import annotations

from dataclasses import dataclass

_STRONG_BOUNDARIES = frozenset("。？！")


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

    def _next_boundary(self, *, final: bool) -> int | None:
        if not self._buffer:
            return None
        min_chars, max_chars = self._limits()

        comma = self._buffer.find("、")
        comma_min_chars = min(min_chars, 10)
        strong = _first_boundary(self._buffer, _STRONG_BOUNDARIES)
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
        candidate.rfind(mark) + 1
        for mark in ("。", "？", "！", "、")
        if candidate.rfind(mark) + 1 >= minimum
    ]
    return max(positions) if positions else None
