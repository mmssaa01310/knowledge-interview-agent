from __future__ import annotations

import re
from dataclasses import dataclass

_STRONG_BOUNDARIES = frozenset("。？！!?\n")
_SAFE_CLAUSE_MARKERS = (
    "具体的には",
    "その一方で",
    "そのため",
    "その結果",
    "一方で",
    "それから",
    "その後",
    "さらに",
    "ただし",
    "次に",
    "また",
    "なお",
)
_COMPLETE_PREDICATE_SUFFIX = re.compile(
    r"(?:していただけますか|してもらえますか|していただけます|"
    r"してもらえます|してください|なっています|となっています|"
    r"していました|しています|しており|している|していた|"
    r"でしょうか|ではありません|ではないです|ありませんでした|"
    r"できました|できています|になります|となります|"
    r"でした|ません|ました|います|あります|します|です|ます|"
    r"だった|である|できる|する|した)$"
)
_PUNCTUATION = frozenset("。？！!?、,\n")


@dataclass(frozen=True)
class PollyTextChunkerConfig:
    first_min_chars: int = 5
    first_target_chars: int = 30
    following_min_chars: int = 10
    following_target_chars: int = 80


class PollyTextChunker:
    """Buffer streamed text until a punctuation or safe phrase boundary."""

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
        minimum, target = self._limits()

        punctuation = _first_punctuation_boundary(self._buffer, minimum)
        if punctuation is not None:
            return punctuation

        if len(self._buffer) >= target:
            safe_clause = _next_safe_clause_boundary(
                self._buffer,
                minimum=minimum,
                target=target,
            )
            if safe_clause is not None:
                return safe_clause

        # Character targets only decide when to look for a safe boundary.
        # They never split a Japanese word, predicate, or particle-bound phrase
        # by themselves. If no natural punctuation or complete-clause boundary
        # has arrived, keep buffering until one does or the response ends.
        if final:
            return len(self._buffer)
        return None

    def _limits(self) -> tuple[int, int]:
        if self._chunk_index == 0:
            return self._config.first_min_chars, self._config.first_target_chars
        return self._config.following_min_chars, self._config.following_target_chars


def split_text_for_polly(
    text: str,
    config: PollyTextChunkerConfig | None = None,
) -> list[str]:
    return PollyTextChunker(config).feed(text, final=True)


def _first_punctuation_boundary(text: str, minimum: int) -> int | None:
    strong = _first_strong_boundary(text)
    comma = text.find("、")
    if comma >= 0 and comma + 1 < minimum:
        comma = -1
    comma_boundary = comma + 1 if comma >= 0 else None
    if strong is None:
        return comma_boundary
    if comma_boundary is None:
        return strong
    return min(strong, comma_boundary)


def _first_strong_boundary(text: str) -> int | None:
    for index, mark in enumerate(text):
        if mark in _STRONG_BOUNDARIES:
            return index + 1
        if mark == "." and _safe_ascii_period(text, index):
            return index + 1
    return None


def _next_safe_clause_boundary(
    text: str,
    *,
    minimum: int,
    target: int,
) -> int | None:
    candidates: list[int] = []
    for marker in _SAFE_CLAUSE_MARKERS:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            if index >= minimum and _has_complete_predicate(text[:index]):
                candidates.append(index)
            start = index + len(marker)

    for match in re.finditer(r"\s+", text):
        index = match.start()
        prefix = text[:index].rstrip()
        if index >= minimum and (
            not _contains_japanese(prefix) or _has_complete_predicate(prefix)
        ):
            candidates.append(index)

    return _boundary_near_target(candidates, target) if candidates else None


def _boundary_near_target(candidates: list[int], target: int) -> int:
    after_target = [boundary for boundary in candidates if boundary >= target]
    return min(after_target) if after_target else max(candidates)


def _has_complete_predicate(text: str) -> bool:
    return bool(_COMPLETE_PREDICATE_SUFFIX.search(text.rstrip()))


def _contains_japanese(text: str) -> bool:
    return any("\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff" for char in text)


def _safe_ascii_period(text: str, index: int) -> bool:
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    # A streamed delta may end after the period in a decimal value such as
    # ``3.14``. Wait for the next delta to reveal whether it is a sentence end.
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
