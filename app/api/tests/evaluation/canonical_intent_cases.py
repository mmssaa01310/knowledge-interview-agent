from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredDialogueAct


@dataclass(frozen=True)
class CanonicalIntentCase:
    """Human-labelled, provider-independent router evaluation input."""

    case_id: str
    expected_intent: StructuredDialogueAct
    utterance: str
    context: str


_CURRENT_QUESTION = {
    "questionId": "question-responsibility",
    "text": "現在の担当領域を教えてください。",
    "targetType": "field",
    "targetId": "field-responsibility",
    "targetLabel": "担当領域",
}
_CURRENT_TARGET = {
    "targetType": "field",
    "targetId": "field-responsibility",
    "label": "担当領域",
}
_EQUIPMENT_QUESTION = {
    "questionId": "question-equipment-response",
    "text": "異常が出たときの確認手順を教えてください。",
    "targetType": "field",
    "targetId": "field-equipment-response",
    "targetLabel": "異常時の確認手順",
}
_EQUIPMENT_TARGET = {
    "targetType": "field",
    "targetId": "field-equipment-response",
    "label": "異常時の確認手順",
}
_PROFILE_QUESTION = {
    "questionId": "question-profile-confirmation",
    "text": "基本プロフィールは、宮崎さんがスマート技術開発部のエンジニアという理解でよろしいですか？",
    "targetType": "field",
    "targetId": "field-profile",
    "targetLabel": "基本プロフィール",
}
_PROFILE_TARGET = {
    "targetType": "field",
    "targetId": "field-profile",
    "label": "基本プロフィール",
}


def build_case_context(case: CanonicalIntentCase) -> dict[str, Any]:
    """Return the narrow context accepted by ``resolve_canonical_intent``.

    The context label is evaluation data, not a production routing rule.  It
    lets the dataset explicitly exercise the same utterance under different
    assistant turns, especially the context-sensitive ``はい`` cases.
    """

    if case.context in {"confirmation", "rejection"}:
        return {
            "current_question": _PROFILE_QUESTION,
            "current_target": _PROFILE_TARGET,
            "pending_confirmation": True,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _PROFILE_QUESTION["text"],
                },
            ),
        }
    if case.context == "backchannel":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": "急がず、思い出せるところからお話しください。",
                },
            ),
        }
    if case.context == "correction":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": "先ほど、担当領域は開発部という理解で記録しました。",
                },
            ),
        }
    if case.context == "conversation_request":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "irrelevant":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "question_to_assistant":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "clarification":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "hesitation":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "answer":
        return {
            "current_question": _CURRENT_QUESTION,
            "current_target": _CURRENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _CURRENT_QUESTION["text"],
                },
            ),
        }
    if case.context == "answer_equipment":
        return {
            "current_question": _EQUIPMENT_QUESTION,
            "current_target": _EQUIPMENT_TARGET,
            "pending_confirmation": False,
            "recent_conversation": (
                {
                    "role": "assistant",
                    "content": _EQUIPMENT_QUESTION["text"],
                },
            ),
        }
    raise ValueError(f"unknown evaluation context: {case.context}")


CASES: tuple[CanonicalIntentCase, ...] = (
    # ANSWER.  The utterance is an answer to the active question, even when
    # it is short or starts with an acknowledgement word.
    CanonicalIntentCase("answer-01", "ANSWER", "宮崎です。スマート技術開発部でエンジニアをしています。", "answer"),
    CanonicalIntentCase("answer-02", "ANSWER", "担当は設備保全で、主に軸受を見ています。", "answer"),
    CanonicalIntentCase("answer-03", "ANSWER", "異常振動が出たら、まず軸受温度を確認します。", "answer_equipment"),
    CanonicalIntentCase("answer-04", "ANSWER", "判断の目安は80度です。", "answer_equipment"),
    CanonicalIntentCase("answer-05", "ANSWER", "現場では三人で交代しながら点検しています。", "answer"),
    CanonicalIntentCase("answer-06", "ANSWER", "はい、現在は品質保証部です。", "answer"),
    CanonicalIntentCase("answer-07", "ANSWER", "昨年からこの設備を担当しています。", "answer"),
    CanonicalIntentCase("answer-08", "ANSWER", "定期点検は毎週月曜日に実施しています。", "answer_equipment"),
    CanonicalIntentCase("answer-09", "ANSWER", "私は大阪工場の保全チームに所属しています。", "answer"),
    CanonicalIntentCase("answer-10", "ANSWER", "主な役割は原因を切り分けて復旧手順を決めることです。", "answer"),

    # CONFIRMATION is only expected when the assistant has presented a
    # candidate/understanding for confirmation.
    CanonicalIntentCase("confirmation-01", "CONFIRMATION", "大丈夫です。", "confirmation"),
    CanonicalIntentCase("confirmation-02", "CONFIRMATION", "はい、それで合っています。", "confirmation"),
    CanonicalIntentCase("confirmation-03", "CONFIRMATION", "はい。", "confirmation"),
    CanonicalIntentCase("confirmation-04", "CONFIRMATION", "その認識で問題ないです。", "confirmation"),
    CanonicalIntentCase("confirmation-05", "CONFIRMATION", "うん、それでいいです。", "confirmation"),
    CanonicalIntentCase("confirmation-06", "CONFIRMATION", "それでOKです。", "confirmation"),
    CanonicalIntentCase("confirmation-07", "CONFIRMATION", "はい、間違いありません。", "confirmation"),
    CanonicalIntentCase("confirmation-08", "CONFIRMATION", "その内容でお願いします。", "confirmation"),
    CanonicalIntentCase("confirmation-09", "CONFIRMATION", "ええ、合っています。", "confirmation"),
    CanonicalIntentCase("confirmation-10", "CONFIRMATION", "問題ありません、そのままで大丈夫です。", "confirmation"),

    # REJECTION is evaluated with a pending candidate as well.
    CanonicalIntentCase("rejection-01", "REJECTION", "違います。", "rejection"),
    CanonicalIntentCase("rejection-02", "REJECTION", "いや、そうではないです。", "rejection"),
    CanonicalIntentCase("rejection-03", "REJECTION", "そこは間違っています。", "rejection"),
    CanonicalIntentCase("rejection-04", "REJECTION", "違う違う。", "rejection"),
    CanonicalIntentCase("rejection-05", "REJECTION", "その理解ではありません。", "rejection"),
    CanonicalIntentCase("rejection-06", "REJECTION", "いえ、その理解は違います。", "rejection"),
    CanonicalIntentCase("rejection-07", "REJECTION", "その候補は採用しません。", "rejection"),
    CanonicalIntentCase("rejection-08", "REJECTION", "うーん、そこは違いますね。", "rejection"),
    CanonicalIntentCase("rejection-09", "REJECTION", "その内容ではないです。", "rejection"),
    CanonicalIntentCase("rejection-10", "REJECTION", "いいえ、そういう意味ではありません。", "rejection"),

    # CLARIFICATION asks what the active question means or what scope it has.
    CanonicalIntentCase("clarification-01", "CLARIFICATION_REQUEST", "担当領域ってどの範囲のこと？", "clarification"),
    CanonicalIntentCase("clarification-02", "CLARIFICATION_REQUEST", "それって何について答えればいい？", "clarification"),
    CanonicalIntentCase("clarification-03", "CLARIFICATION_REQUEST", "ここでいう経験って何の経験？", "clarification"),
    CanonicalIntentCase("clarification-04", "CLARIFICATION_REQUEST", "関わった相手って、何で関わった相手？", "clarification"),
    CanonicalIntentCase("clarification-05", "CLARIFICATION_REQUEST", "どこまで詳しく話せばいいですか？", "clarification"),
    CanonicalIntentCase("clarification-06", "CLARIFICATION_REQUEST", "担当領域というのは役職のことですか？", "clarification"),
    CanonicalIntentCase("clarification-07", "CLARIFICATION_REQUEST", "具体的に何を聞いていますか？", "clarification"),
    CanonicalIntentCase("clarification-08", "CLARIFICATION_REQUEST", "この質問の対象はどの作業ですか？", "clarification"),
    CanonicalIntentCase("clarification-09", "CLARIFICATION_REQUEST", "名前だけ答えればよいですか？", "clarification"),
    CanonicalIntentCase("clarification-10", "CLARIFICATION_REQUEST", "異常と判断する基準について、どの場面を話せばいい？", "clarification"),

    # QUESTION_TO_ASSISTANT addresses the interviewer or challenges the
    # conversation itself; it is distinct from asking for a target's scope.
    CanonicalIntentCase("question-to-assistant-01", "QUESTION_TO_ASSISTANT", "いや、あなたが質問しないからなんもわかんないんだけど。", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-02", "QUESTION_TO_ASSISTANT", "今、何を知りたいんですか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-03", "QUESTION_TO_ASSISTANT", "あなたは私に何を答えてほしいの？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-04", "QUESTION_TO_ASSISTANT", "質問文が表示されていないようですが？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-05", "QUESTION_TO_ASSISTANT", "どうしてそのことを聞くんですか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-06", "QUESTION_TO_ASSISTANT", "このインタビューでは何を確認するんですか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-07", "QUESTION_TO_ASSISTANT", "さっきの質問、答えを聞いていますか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-08", "QUESTION_TO_ASSISTANT", "もう質問は出ていますか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-09", "QUESTION_TO_ASSISTANT", "その質問は私に必要ですか？", "question_to_assistant"),
    CanonicalIntentCase("question-to-assistant-10", "QUESTION_TO_ASSISTANT", "何を答えたら次に進めますか？", "question_to_assistant"),

    # CORRECTION changes or repairs a previous statement/understanding.
    CanonicalIntentCase("correction-01", "CORRECTION", "え、すでに回答しているけど。", "correction"),
    CanonicalIntentCase("correction-02", "CORRECTION", "だから回答してるって。", "correction"),
    CanonicalIntentCase("correction-03", "CORRECTION", "先ほどの部署は開発部ではなく品質保証部です。", "correction"),
    CanonicalIntentCase("correction-04", "CORRECTION", "訂正します、担当は保全ではなく設計です。", "correction"),
    CanonicalIntentCase("correction-05", "CORRECTION", "今の記録は少し違うので直してください。", "correction"),
    CanonicalIntentCase("correction-06", "CORRECTION", "さっきの数字は80ではなく90度です。", "correction"),
    CanonicalIntentCase("correction-07", "CORRECTION", "言い方が違いました、軸受温度のことです。", "correction"),
    CanonicalIntentCase("correction-08", "CORRECTION", "前に答えた内容を修正したいです。", "correction"),
    CanonicalIntentCase("correction-09", "CORRECTION", "それではなく、別の設備について話しています。", "correction"),
    CanonicalIntentCase("correction-10", "CORRECTION", "私の回答を取り違えています。正しくは大阪工場です。", "correction"),

    # HESITATION represents thinking/holding the turn, not an answer failure.
    CanonicalIntentCase("hesitation-01", "HESITATION", "えーっと……", "hesitation"),
    CanonicalIntentCase("hesitation-02", "HESITATION", "ちょっと考えます。", "hesitation"),
    CanonicalIntentCase("hesitation-03", "HESITATION", "うーん……", "hesitation"),
    CanonicalIntentCase("hesitation-04", "HESITATION", "少し待ってください。", "hesitation"),
    CanonicalIntentCase("hesitation-05", "HESITATION", "ええと、どうだったかな。", "hesitation"),
    CanonicalIntentCase("hesitation-06", "HESITATION", "思い出しているところです。", "hesitation"),
    CanonicalIntentCase("hesitation-07", "HESITATION", "ちょっと整理しますね。", "hesitation"),
    CanonicalIntentCase("hesitation-08", "HESITATION", "うーん、すぐには出てこないです。", "hesitation"),
    CanonicalIntentCase("hesitation-09", "HESITATION", "どうだったか確認します。", "hesitation"),
    CanonicalIntentCase("hesitation-10", "HESITATION", "少し考える時間をください。", "hesitation"),

    # BACKCHANNEL is an acknowledgement of the assistant, not a candidate
    # confirmation and not a substantive answer.
    CanonicalIntentCase("backchannel-01", "BACKCHANNEL", "はい。", "backchannel"),
    CanonicalIntentCase("backchannel-02", "BACKCHANNEL", "なるほど。", "backchannel"),
    CanonicalIntentCase("backchannel-03", "BACKCHANNEL", "うんうん。", "backchannel"),
    CanonicalIntentCase("backchannel-04", "BACKCHANNEL", "そうなんですね。", "backchannel"),
    CanonicalIntentCase("backchannel-05", "BACKCHANNEL", "へえ。", "backchannel"),
    CanonicalIntentCase("backchannel-06", "BACKCHANNEL", "はいはい。", "backchannel"),
    CanonicalIntentCase("backchannel-07", "BACKCHANNEL", "分かりました。", "backchannel"),
    CanonicalIntentCase("backchannel-08", "BACKCHANNEL", "そうですか。", "backchannel"),
    CanonicalIntentCase("backchannel-09", "BACKCHANNEL", "聞いています。", "backchannel"),
    CanonicalIntentCase("backchannel-10", "BACKCHANNEL", "はい、聞いています。", "backchannel"),

    # IRRELEVANT is unrelated content while an interview question is active.
    CanonicalIntentCase("irrelevant-01", "IRRELEVANT", "今日は雨が降りそうですね。", "irrelevant"),
    CanonicalIntentCase("irrelevant-02", "IRRELEVANT", "昼ごはんは何にしましょうか。", "irrelevant"),
    CanonicalIntentCase("irrelevant-03", "IRRELEVANT", "昨日の試合を見ましたか？", "irrelevant"),
    CanonicalIntentCase("irrelevant-04", "IRRELEVANT", "この部屋は少し寒いですね。", "irrelevant"),
    CanonicalIntentCase("irrelevant-05", "IRRELEVANT", "週末は旅行に行く予定です。", "irrelevant"),
    CanonicalIntentCase("irrelevant-06", "IRRELEVANT", "スマートフォンの充電が切れそうです。", "irrelevant"),
    CanonicalIntentCase("irrelevant-07", "IRRELEVANT", "その音楽、好きな曲です。", "irrelevant"),
    CanonicalIntentCase("irrelevant-08", "IRRELEVANT", "今朝は電車が混んでいました。", "irrelevant"),
    CanonicalIntentCase("irrelevant-09", "IRRELEVANT", "この近くにコンビニはありますか？", "irrelevant"),
    CanonicalIntentCase("irrelevant-10", "IRRELEVANT", "猫を飼っているんです。", "irrelevant"),

    # CONVERSATION_REQUEST controls the interview/session rather than
    # answering the active target.
    CanonicalIntentCase("conversation-request-01", "CONVERSATION_REQUEST", "おーい。", "conversation_request"),
    CanonicalIntentCase("conversation-request-02", "CONVERSATION_REQUEST", "こんにちは。", "conversation_request"),
    CanonicalIntentCase("conversation-request-03", "CONVERSATION_REQUEST", "一度止めてもらえますか？", "conversation_request"),
    CanonicalIntentCase("conversation-request-04", "CONVERSATION_REQUEST", "今日はここで終わりにしたいです。", "conversation_request"),
    CanonicalIntentCase("conversation-request-05", "CONVERSATION_REQUEST", "インタビューを一旦休憩したいです。", "conversation_request"),
    CanonicalIntentCase("conversation-request-06", "CONVERSATION_REQUEST", "次の質問に進めてください。", "conversation_request"),
    CanonicalIntentCase("conversation-request-07", "CONVERSATION_REQUEST", "録音を止めてください。", "conversation_request"),
    CanonicalIntentCase("conversation-request-08", "CONVERSATION_REQUEST", "最初からやり直せますか？", "conversation_request"),
    CanonicalIntentCase("conversation-request-09", "CONVERSATION_REQUEST", "もう少しゆっくり話してください。", "conversation_request"),
    CanonicalIntentCase("conversation-request-10", "CONVERSATION_REQUEST", "ここでインタビューを終了できますか？", "conversation_request"),
)


if len(CASES) != 100:  # Keep accidental dataset shrinkage visible to evaluators.
    raise AssertionError(f"canonical intent evaluation requires 100 cases, got {len(CASES)}")
