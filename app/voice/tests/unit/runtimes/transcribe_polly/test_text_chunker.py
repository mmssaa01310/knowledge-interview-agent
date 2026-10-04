import pytest

from ai_interviewer_voice.runtimes.transcribe_polly.text_chunker import (
    PollyTextChunker,
    PollyTextChunkerConfig,
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


def test_incremental_chunker_emits_a_natural_comma_boundary_immediately() -> None:
    chunker = PollyTextChunker()

    assert chunker.feed("設備が停止する直前に") == []
    assert chunker.feed("、異音や振動は") == ["設備が停止する直前に、"]
    assert chunker.feed("ありましたか？", final=True) == [
        "異音や振動はありましたか？"
    ]


def test_role_and_responsibility_question_keeps_predicates_intact() -> None:
    question = "現在の役割と、日々どのような責任を担っているかを具体的に教えてください。"
    chunker = PollyTextChunker()

    assert chunker.push("現在の役割と") == []
    assert chunker.push("、") == ["現在の役割と、"]
    assert chunker.push("日々どのような責任を") == []
    assert chunker.push("担っているかを具体的に教えて") == []
    chunks = ["現在の役割と、", *chunker.push("ください。", final=True)]

    assert chunks == [
        "現在の役割と、",
        "日々どのような責任を担っているかを具体的に教えてください。",
    ]
    assert "".join(chunks) == question
    assert not any(
        left.endswith("責任を") and right.startswith("担っている")
        or left.endswith("教えて") and right.startswith("ください")
        for left, right in zip(chunks, chunks[1:])
    )


def test_multiple_japanese_questions_keep_their_first_natural_boundary() -> None:
    questions = [
        "現在の役割と、日々どのような責任を担っているかを具体的に教えてください。",
        "現在の業務内容をどのように整理し、関係者と共有していますか？",
    ]

    assert [split_text_for_polly(question) for question in questions] == [
        [
            "現在の役割と、",
            "日々どのような責任を担っているかを具体的に教えてください。",
        ],
        [
            "現在の業務内容をどのように整理し、",
            "関係者と共有していますか？",
        ],
    ]


def test_chunker_does_not_force_a_split_at_the_character_target() -> None:
    chunker = PollyTextChunker(
        PollyTextChunkerConfig(
            first_min_chars=3,
            first_target_chars=8,
            following_min_chars=3,
            following_target_chars=10,
        )
    )

    assert chunker.feed("あ" * 100) == []
    assert chunker.feed("", final=True) == ["あ" * 100]


def test_long_buffer_uses_a_complete_predicate_before_a_discourse_marker() -> None:
    chunker = PollyTextChunker(
        PollyTextChunkerConfig(
            first_min_chars=5,
            first_target_chars=14,
            following_min_chars=5,
            following_target_chars=80,
        )
    )

    assert chunker.feed("担当者が課題を確認していますまた次の担当者に共有します") == [
        "担当者が課題を確認しています"
    ]
    assert chunker.feed("", final=True) == ["また次の担当者に共有します"]


def test_long_japanese_text_does_not_cut_after_a_particle_without_a_safe_boundary() -> None:
    chunker = PollyTextChunker(
        PollyTextChunkerConfig(
            first_min_chars=5,
            first_target_chars=18,
            following_min_chars=5,
            following_target_chars=80,
        )
    )
    text = "顧客の要望を整理し関係者と優先順位を確認して実装しています"

    assert chunker.feed(text) == []
    assert chunker.feed("", final=True) == [text]


@pytest.mark.parametrize(
    "text",
    [
        "この対応をしてください。",
        "毎日の点検を担当しています。",
        "この機能はすでに本番環境で利用できる状態になっています。",
        "担当者はこの工程全体を担っています。",
        "この手順でよいでしょうか？",
        "詳しい経緯を教えてもらえますか？",
    ],
)
def test_streaming_does_not_force_a_cut_inside_japanese_predicates(text: str) -> None:
    chunker = PollyTextChunker(
        PollyTextChunkerConfig(
            first_min_chars=2,
            first_target_chars=4,
            following_min_chars=2,
            following_target_chars=5,
        )
    )

    assert chunker.push(text[:-2]) == []
    assert chunker.push(text[-2:], final=True) == [text]


@pytest.mark.parametrize(
    "text",
    [
        "現在の役割と、日々どのような責任を担っているかを具体的に教えてください。",
        "現在の業務内容をどのように整理し、関係者と共有していますか？",
        "毎日の点検を担当しています。",
        "この手順はどのような場面で必要になるでしょうか？",
        "詳しい経緯を教えてもらえますか？",
        "現在の役割や責任について具体的に教えてください",
    ],
)
def test_japanese_question_fixtures_never_split_inside_requested_predicates(text: str) -> None:
    chunks = split_text_for_polly(text)

    assert "".join(chunks) == text
    for left, right in zip(chunks, chunks[1:]):
        assert not (left.endswith("責任を") and right.startswith("担っている"))
        assert not (left.endswith("教えて") and right.startswith("ください"))
        assert not (left.endswith("教えてもらえ") and right.startswith("ますか"))


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
