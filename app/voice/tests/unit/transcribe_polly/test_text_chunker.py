from ai_interviewer_voice.runtimes.transcribe_polly.text_chunker import (
    PollyTextChunker,
    split_text_for_polly,
)

def test_split_text_prefers_sentence_and_comma_boundaries() -> None:
    chunks = split_text_for_polly(
        "ありがとうございます。設備が停止する直前に、異音や振動はありましたか？"
    )

    assert chunks == [
        "ありがとうございます。",
        "設備が停止する直前に、",
        "異音や振動はありましたか？",
    ]


def test_incremental_chunker_keeps_incomplete_text_until_final() -> None:
    chunker = PollyTextChunker()

    assert chunker.feed("設備が停止する直前に") == []
    assert chunker.feed("、異音や振動は") == ["設備が停止する直前に、"]
    assert chunker.feed("ありましたか？", final=True) == ["異音や振動はありましたか？"]


def test_chunker_never_exceeds_eighty_characters() -> None:
    chunks = split_text_for_polly("あ" * 190)

    assert "".join(chunks) == "あ" * 190
    assert max(map(len, chunks)) <= 80


def test_incremental_chunker_supports_newlines_and_safe_ascii_punctuation() -> None:
    chunker = PollyTextChunker()

    assert chunker.push("The value is 3.14 and the next step is ready. ") == [
        "The value is 3.14 and the next step is ready."
    ]
    assert chunker.push("URL is https://example.com/path and continue") == []
    assert chunker.push("\n次の確認をお願いします。", final=True) == [
        "URL is https://example.com/path and continue",
        "次の確認をお願いします。",
    ]


def test_incremental_chunker_does_not_split_decimal_across_deltas() -> None:
    chunker = PollyTextChunker()

    assert chunker.push("値は3.") == []
    assert chunker.push("14です。", final=True) == ["値は3.14です。"]
