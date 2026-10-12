"""Voice-only wording for transcript retry and correction feedback.

The Structured Interview service remains the source of truth for interpreting
and persisting an answer. This module only turns that result into a concise
spoken response for the Transcribe + Polly transport. It deliberately never
uses the raw transcript as user-facing evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    localized_interview_ambiguous_part_prompt,
    localized_interview_not_heard_prompt,
)


_MAX_SPOKEN_VALUE_LENGTH = 64
_MAX_CANDIDATE_LENGTH = 120


def build_transcribe_polly_transcript_feedback(
    result: Mapping[str, Any],
    turn: Mapping[str, Any],
    *,
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
) -> str | None:
    """Build a targeted spoken retry for a Transcribe + Polly turn.

    ``None`` means that the normal Structured Interview reply should be used.
    The function is presentation-only: it does not change state, promote a
    candidate, or decide whether a transcript is safe to store.
    """

    state = _as_mapping(result.get("interviewState"))
    assessment = _as_mapping(state.get("lastTranscriptAssessment"))
    output = _as_mapping(state.get("lastStructuredOutput"))
    question = _as_mapping(result.get("question"))
    status = _clean_text(assessment.get("correctionStatus")).upper()
    answer_evaluation = _clean_text(
        output.get("answerEvaluation") or result.get("answerEvaluation")
    ).upper()
    correction_candidates = _unique_texts(assessment.get("correctionCandidates"))

    if status in {"CORRECTED", "UNCERTAIN"}:
        candidate_items = _correction_candidate_items(
            state,
            assessment,
            field_labels=field_labels,
            locale=locale,
        )
        if candidate_items and (status == "UNCERTAIN" or len(correction_candidates) <= 1):
            return _correction_confirmation_message(candidate_items, locale)

        clarification_target = _as_mapping(
            state.get("lastClarificationTarget")
            or result.get("clarificationTarget")
        )
        semantic_role = _clean_text(clarification_target.get("semanticRole"))
        if not semantic_role:
            ambiguous_spans = assessment.get("ambiguousSpans")
            if isinstance(ambiguous_spans, list):
                semantic_role = next(
                    (
                        _clean_text(_as_mapping(span).get("semanticRole"))
                        for span in ambiguous_spans
                        if _as_mapping(span).get("important", True)
                        and _clean_text(_as_mapping(span).get("semanticRole"))
                    ),
                    "",
                )
        if semantic_role:
            return localized_interview_ambiguous_part_prompt(
                locale,
                semantic_role,
                target_label=_clean_text(clarification_target.get("label")) or None,
            )

        labels = _confirmation_labels(state, question, field_labels, locale)
        if labels:
            return localized_interview_not_heard_prompt(
                locale,
                _join_labels(labels, locale),
            )
        source_question = _source_question_text(state, question)
        if source_question:
            return _repeat_source_question_message(locale, source_question)
        return localized_interview_not_heard_prompt(locale, _previous_question_label(locale))

    # The shared evaluator is authoritative when it is available. Semantic
    # insufficiency, off-target answers, and incomplete utterances must never
    # be relabeled as an audio failure by this presentation layer. The legacy
    # status-only fallback remains for old persisted turns without the new
    # evaluation field.
    if answer_evaluation and answer_evaluation != "NOT_HEARD":
        return None
    if status != "UNCERTAIN" and answer_evaluation != "NOT_HEARD":
        return None

    clarification_target = _as_mapping(
        state.get("lastClarificationTarget")
        or result.get("clarificationTarget")
    )
    if not _has_concrete_asr_target(clarification_target, assessment):
        # The shared coordinator does not initiate ASR retry without a named
        # span.  Keep this presentation layer aligned with that invariant for
        # legacy or malformed persisted state as well.
        return None
    latest_message_ids = _latest_message_ids(state, turn)
    reliable_updates = _reliable_field_updates(
        output,
        latest_message_ids=latest_message_ids,
        field_labels=field_labels,
        correction_candidates=correction_candidates,
    )
    understood = _understood_phrase(reliable_updates, field_labels, locale)
    if clarification_target:
        target_type = _clean_text(clarification_target.get("type")).upper()
        if target_type == "ASR_AMBIGUOUS" and _clean_text(
            clarification_target.get("semanticRole")
        ):
            return localized_interview_ambiguous_part_prompt(
                locale,
                _clean_text(clarification_target.get("semanticRole")) or None,
                understood,
                target_label=_clean_text(clarification_target.get("label")) or None,
            )

    # A persisted turn from before clarificationTarget was introduced may
    # still carry the span metadata on the transcript assessment itself.
    ambiguous_spans = assessment.get("ambiguousSpans")
    if isinstance(ambiguous_spans, list):
        important_span = next(
            (
                _as_mapping(item)
                for item in ambiguous_spans
                if _as_mapping(item).get("important", True)
            ),
            None,
        )
        if important_span:
            return localized_interview_ambiguous_part_prompt(
                locale,
                _clean_text(important_span.get("semanticRole")) or None,
                understood,
            )

    unclear_labels = _unclear_labels(
        state,
        question,
        field_labels,
        known_field_ids=set(reliable_updates),
        locale=locale,
    )
    if understood and unclear_labels:
        unclear = _join_labels(unclear_labels, locale)
        if locale == "en-US":
            return (
                f"I understood {understood}. I couldn't hear {unclear}. "
                f"Please repeat only {unclear}."
            )
        if locale == "zh-CN":
            return f"我听懂了{understood}。{unclear}没有听清，请只重复{unclear}。"
        if locale == "pt-BR":
            return (
                f"Entendi {understood}. Não consegui ouvir {unclear}. "
                f"Por favor, repita apenas {unclear}."
            )
        return (
            f"{understood}ということは分かりました。{unclear}がうまく聞き取れなかったので、"
            f"{unclear}だけもう一度お願いします。"
        )

    subject = _retry_subject(
        unclear_labels,
        question,
        state,
        field_labels,
        locale,
        prefer_broad=not bool(understood),
    )
    if _is_broad_target(subject):
        return localized_interview_ambiguous_part_prompt(locale)
    if understood and not unclear_labels:
        return _repeat_unclear_part_message(locale, understood, subject)
    if understood:
        if locale == "en-US":
            return f"I understood {understood}. Please repeat the part about {subject}."
        if locale == "zh-CN":
            return f"我听懂了{understood}。请再说明一下{subject}。"
        if locale == "pt-BR":
            return f"Entendi {understood}. Por favor, repita a parte sobre {subject}."
        return f"{understood}ということは分かりました。{subject}について、もう一度お願いします。"

    if correction_candidates:
        return _partial_retry_message(locale, subject)

    if locale == "en-US":
        return f"I couldn't hear your answer clearly. Please tell me about {subject} again."
    if locale == "zh-CN":
        return f"回答没有听清楚。请再说一遍{subject}。"
    if locale == "pt-BR":
        return (
            "Não consegui ouvir sua resposta com clareza. "
            f"Por favor, fale novamente sobre {subject}."
        )
    return f"回答がうまく聞き取れませんでした。もう一度、{subject}についてお話しください。"


def _partial_retry_message(locale: InterviewLocale, subject: str) -> str:
    if locale == "en-US":
        return (
            f"I couldn't hear part of your answer about {subject} clearly. "
            "Please repeat only the unclear part."
        )
    if locale == "zh-CN":
        return f"关于{subject}的回答中有一部分没有听清楚。请只重复没有听清的部分。"
    if locale == "pt-BR":
        return (
            f"Não consegui ouvir uma parte da sua resposta sobre {subject}. "
            "Por favor, repita apenas essa parte."
        )
    return (
        f"{subject}について、回答の一部がうまく聞き取れませんでした。"
        "聞き取れなかった部分だけ、もう一度お願いします。"
    )


def _repeat_unclear_part_message(
    locale: InterviewLocale,
    understood: str,
    subject: str,
) -> str:
    if locale == "en-US":
        return f"I understood {understood}. Please repeat only the unclear part about {subject}."
    if locale == "zh-CN":
        return f"我听懂了{understood}。请只重复{subject}中没有听清的部分。"
    if locale == "pt-BR":
        return f"Entendi {understood}. Por favor, repita apenas a parte pouco clara sobre {subject}."
    return f"{understood}ということは分かりました。{subject}のうち、聞き取れなかった部分だけもう一度お願いします。"


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _has_concrete_asr_target(
    clarification_target: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> bool:
    if _clean_text(clarification_target.get("type")).upper() == "ASR_AMBIGUOUS":
        return bool(
            _clean_text(clarification_target.get("span"))
            and _clean_text(clarification_target.get("semanticRole"))
        )
    spans = assessment.get("ambiguousSpans")
    if not isinstance(spans, list):
        return False
    return any(
        _as_mapping(item).get("important", True)
        and _clean_text(_as_mapping(item).get("span"))
        and _clean_text(_as_mapping(item).get("semanticRole"))
        for item in spans
    )


def _unique_texts(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _latest_message_ids(
    state: Mapping[str, Any],
    turn: Mapping[str, Any],
) -> set[str]:
    result: set[str] = set()
    state_message_id = _clean_text(state.get("lastProcessedUserMessageId"))
    if state_message_id:
        result.add(state_message_id)
    turn_id = _clean_text(turn.get("id"))
    if turn_id:
        result.add(f"voice-msg-{turn_id}")
    return result


def _reliable_field_updates(
    output: Mapping[str, Any],
    *,
    latest_message_ids: set[str],
    field_labels: Mapping[str, str],
    correction_candidates: list[str],
) -> dict[str, str]:
    updates = output.get("fieldUpdates")
    if not isinstance(updates, list):
        return {}
    reliable: dict[str, str] = {}
    for item in updates:
        update = _as_mapping(item)
        field_id = _clean_text(update.get("fieldId"))
        value = _clean_text(update.get("value"))
        evidence_ids = {
            _clean_text(evidence_id)
            for evidence_id in (update.get("evidenceTranscriptIds") or [])
            if _clean_text(evidence_id)
        }
        if (
            not field_id
            or not value
            or not _clean_text(field_labels.get(field_id))
            or not evidence_ids.intersection(latest_message_ids)
            or _clean_text(update.get("candidateSource")) == "document_reference"
            or _clean_text(update.get("answerResolution"))
            not in {"AUTO_CONFIRM", "TENTATIVE"}
            or value in correction_candidates
        ):
            continue
        reliable[field_id] = value
    return reliable


def _confirmation_labels(
    state: Mapping[str, Any],
    question: Mapping[str, Any],
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
) -> list[str]:
    pending = _as_mapping(state.get("pendingTranscriptConfirmation"))
    labels: list[str] = []
    target_refs = pending.get("targetRefs")
    if isinstance(target_refs, list):
        for item in target_refs:
            target = _as_mapping(item)
            target_id = _clean_text(target.get("targetId"))
            label = _label_for_target(
                state,
                target.get("targetType"),
                target_id,
                field_labels,
            )
            if label:
                labels.append(label)
    if not labels:
        target_id = _clean_text(question.get("targetId"))
        label = _label_for_target(
            state,
            question.get("targetType") or question.get("kind"),
            target_id,
            field_labels,
        )
        label = label or _clean_text(question.get("targetLabel") or question.get("label"))
        if label:
            labels.append(label)
    return _unique_labels(labels, locale)


def _source_question_text(
    state: Mapping[str, Any],
    question: Mapping[str, Any],
) -> str:
    pending = _as_mapping(state.get("pendingTranscriptConfirmation"))
    source_question = _as_mapping(pending.get("sourceQuestion"))
    text = _clean_text(source_question.get("text"))
    if text:
        return text[:240]

    # A transcript-confirmation question may contain an unsafe summary. Use
    # the current question text only when it is the original interview item.
    if _clean_text(question.get("targetType")) != "transcript_confirmation":
        return _clean_text(question.get("text"))[:240]
    return ""


def _repeat_source_question_message(locale: InterviewLocale, question: str) -> str:
    if locale == "en-US":
        return f"I couldn't confirm your answer. Please answer this question again: {question}"
    if locale == "zh-CN":
        return f"我没能确认您的回答。请重新回答这个问题：{question}"
    if locale == "pt-BR":
        return f"Não consegui confirmar sua resposta. Por favor, responda novamente a esta pergunta: {question}"
    return f"回答を確認できませんでした。先ほどの質問「{question}」にもう一度お答えください。"


def _previous_question_label(locale: InterviewLocale) -> str:
    return {
        "ja-JP": "先ほどの質問",
        "en-US": "the previous question",
        "zh-CN": "刚才的问题",
        "pt-BR": "a pergunta anterior",
    }[locale]


def _correction_candidate_items(
    state: Mapping[str, Any],
    assessment: Mapping[str, Any],
    *,
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
) -> list[tuple[str, str]]:
    pending = _as_mapping(state.get("pendingTranscriptConfirmation"))
    target_refs = pending.get("targetRefs")
    if not isinstance(target_refs, list) or not target_refs:
        return []

    message_id = _clean_text(pending.get("messageId"))
    legacy_mapping = pending.get("candidateMappingVersion") != 1
    unique_target_count = len(
        {
            (
                _clean_text(_as_mapping(item).get("targetType")),
                _clean_text(_as_mapping(item).get("targetId")),
            )
            for item in target_refs
            if _clean_text(_as_mapping(item).get("targetId"))
        }
    )
    full_transcript_values = {
        re.sub(r"\s+", "", _clean_text(value))
        for value in (
            pending.get("normalizedTranscript"),
            pending.get("rawTranscript"),
            assessment.get("normalizedTranscript"),
            assessment.get("rawTranscript"),
        )
        if _clean_text(value)
    }
    field_states = state.get("fieldStates")
    requirement_states = state.get("requirementStates")
    candidates: list[tuple[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()

    def add_candidate(
        label: str,
        value: object,
        *,
        reject_full_transcript: bool,
    ) -> None:
        normalized_value = _clean_text(value)
        if (
            not label
            or not normalized_value
            or (
                reject_full_transcript
                and re.sub(r"\s+", "", normalized_value) in full_transcript_values
            )
        ):
            return
        display_value = _display_value(normalized_value)
        if not display_value:
            return
        item = (_spoken_label(label, locale), display_value)
        if item[0] and item not in candidates:
            candidates.append(item)

    for item in target_refs:
        target = _as_mapping(item)
        target_id = _clean_text(target.get("targetId"))
        target_type = _clean_text(target.get("targetType"))
        target_key = (target_type, target_id)
        if not target_id or target_key in seen_targets:
            continue
        seen_targets.add(target_key)

        if target_type == "field" and isinstance(field_states, Mapping):
            target_state = _as_mapping(field_states.get(target_id))
            plan = _as_mapping(target_state.get("questionPlan"))
            required_items = plan.get("requiredItems")
            item_labels: dict[str, str] = {}
            if isinstance(required_items, list):
                for required_item in required_items:
                    item = _as_mapping(required_item)
                    item_id = _clean_text(item.get("itemId"))
                    label = _clean_text(item.get("label"))
                    if item_id and label:
                        item_labels[item_id] = label
            candidate_items = target_state.get("candidateItems")
            if isinstance(candidate_items, list):
                for candidate_item in candidate_items:
                    candidate = _as_mapping(candidate_item)
                    candidate_evidence_ids = candidate.get("evidenceTranscriptIds")
                    evidence_ids = {
                        _clean_text(evidence_id)
                        for evidence_id in (
                            candidate_evidence_ids
                            if isinstance(candidate_evidence_ids, list)
                            else []
                        )
                        if _clean_text(evidence_id)
                    }
                    if message_id and message_id not in evidence_ids:
                        continue
                    item_id = _clean_text(candidate.get("itemId"))
                    label = item_labels.get(item_id, "")
                    if not label and len(item_labels) <= 1:
                        label = _clean_text(field_labels.get(target_id))
                    add_candidate(
                        label,
                        candidate.get("value"),
                        reject_full_transcript=(
                            legacy_mapping
                            or unique_target_count > 1
                            or len(item_labels) > 1
                        ),
                    )
        elif target_type in {"requirement", "process"} and isinstance(
            requirement_states, Mapping
        ):
            target_state = _as_mapping(requirement_states.get(target_id))
            requirement_evidence_ids = target_state.get("evidenceTranscriptIds")
            evidence_ids = {
                _clean_text(evidence_id)
                for evidence_id in (
                    requirement_evidence_ids
                    if isinstance(requirement_evidence_ids, list)
                    else []
                )
                if _clean_text(evidence_id)
            }
            if message_id and message_id not in evidence_ids:
                continue
            add_candidate(
                _clean_text(target_state.get("label")),
                target_state.get("candidateValue"),
                reject_full_transcript=(legacy_mapping or unique_target_count > 1),
            )

    if sum(len(label) + len(value) for label, value in candidates) > _MAX_CANDIDATE_LENGTH:
        return []
    return candidates


def _unclear_labels(
    state: Mapping[str, Any],
    question: Mapping[str, Any],
    field_labels: Mapping[str, str],
    *,
    known_field_ids: set[str],
    locale: InterviewLocale,
) -> list[str]:
    target_id = _clean_text(question.get("targetId"))
    target_label = _label_for_target(
        state,
        question.get("targetType") or question.get("kind"),
        target_id,
        field_labels,
    )
    target_label = target_label or _clean_text(
        question.get("targetLabel") or question.get("label")
    )

    if _is_broad_target(target_label):
        field_states = state.get("fieldStates")
        if isinstance(field_states, Mapping):
            labels = [
                _spoken_label(field_labels.get(str(field_id)), locale)
                for field_id, field_state in field_states.items()
                if (
                    str(field_id) not in known_field_ids
                    and str(field_id) != target_id
                    and _as_mapping(field_state).get("answerState") != "CONFIRMED"
                    and _clean_text(field_labels.get(str(field_id)))
                    and not _is_broad_target(
                        _spoken_label(field_labels.get(str(field_id)), locale)
                    )
                )
            ]
            labels = _unique_labels(labels, locale)
            if labels:
                return labels

    if target_label and target_id not in known_field_ids:
        return [_spoken_label(target_label, locale)]
    return []


def _retry_subject(
    unclear_labels: list[str],
    question: Mapping[str, Any],
    state: Mapping[str, Any],
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
    *,
    prefer_broad: bool,
) -> str:
    target_id = _clean_text(question.get("targetId"))
    target_label = _label_for_target(
        state,
        question.get("targetType") or question.get("kind"),
        target_id,
        field_labels,
    )
    target_label = target_label or _clean_text(
        question.get("targetLabel") or question.get("label")
    )
    if prefer_broad and _is_broad_target(target_label):
        return _spoken_label(target_label, locale)
    if unclear_labels:
        return _join_labels(unclear_labels, locale)
    if target_label:
        return _spoken_label(target_label, locale)
    if locale == "en-US":
        return "the item you were answering"
    if locale == "zh-CN":
        return "刚才的问题"
    if locale == "pt-BR":
        return "o item que você estava respondendo"
    return "先ほどの項目"


def _label_for_target(
    state: Mapping[str, Any],
    target_type: object,
    target_id: str,
    field_labels: Mapping[str, str],
) -> str:
    normalized_type = _clean_text(target_type)
    if normalized_type == "field" and target_id:
        return _clean_text(field_labels.get(target_id))
    if normalized_type in {"requirement", "process"} and target_id:
        requirement_states = state.get("requirementStates")
        requirement = _as_mapping(
            requirement_states.get(target_id) if isinstance(requirement_states, Mapping) else None
        )
        return _clean_text(requirement.get("label"))
    return ""


def _understood_phrase(
    updates: Mapping[str, str],
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
) -> str:
    parts: list[str] = []
    for field_id, value in updates.items():
        label = _spoken_label(field_labels.get(field_id), locale)
        if not label:
            continue
        display_value = _display_value(value)
        if display_value:
            if locale == "en-US":
                parts.append(f"{label} was {display_value!r}")
            elif locale == "zh-CN":
                parts.append(f"{label}是“{display_value}”")
            elif locale == "pt-BR":
                parts.append(f"{label} é {display_value!r}")
            else:
                parts.append(f"{label}は「{display_value}」")
        elif locale == "en-US":
            parts.append(f"I could hear your {label}")
        elif locale == "zh-CN":
            parts.append(f"{label}听清了")
        elif locale == "pt-BR":
            parts.append(f"consegui ouvir {label}")
        else:
            parts.append(f"{label}は聞き取れました")

    if not parts:
        return ""
    if locale == "ja-JP":
        return "、".join(parts)
    return _join_labels(parts, locale)


def _display_value(value: str) -> str | None:
    normalized = _clean_text(value).strip(" 、。．,.")
    for suffix in (
        "を担当しております",
        "を担当しています",
        "に所属しております",
        "に所属しています",
        "でした",
        "です",
    ):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip(" 、")
            break
    if not normalized or len(normalized) > _MAX_SPOKEN_VALUE_LENGTH:
        return None
    return normalized


def _spoken_label(value: object, locale: InterviewLocale) -> str:
    label = _clean_text(value)
    if not label:
        return ""
    if locale != "ja-JP":
        return label
    compact = re.sub(r"\s+", "", label)
    if any(token in compact for token in ("氏名", "名前", "お名前")):
        return "お名前"
    if any(token in compact for token in ("所属", "部署", "部門")):
        return "所属"
    if any(token in compact for token in ("担当領域", "担当業務", "担当", "業務", "責任")):
        return "担当業務"
    return label


def _is_broad_target(label: str) -> bool:
    compact = re.sub(r"\s+", "", label).lower()
    return any(
        token in compact
        for token in ("プロフィール", "自己紹介", "基本情報", "profile", "introduction")
    )


def _unique_labels(labels: list[str], locale: InterviewLocale) -> list[str]:
    result: list[str] = []
    for label in labels:
        normalized = _spoken_label(label, locale)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _join_labels(labels: list[str], locale: InterviewLocale) -> str:
    if len(labels) == 1:
        return labels[0]
    if locale == "en-US":
        return ", ".join(labels[:-1]) + f" and {labels[-1]}"
    if locale == "zh-CN":
        return "、".join(labels)
    if locale == "pt-BR":
        return ", ".join(labels[:-1]) + f" e {labels[-1]}"
    return "、".join(labels[:-1]) + f"と{labels[-1]}"


def _correction_confirmation_message(
    items: list[tuple[str, str]],
    locale: InterviewLocale,
) -> str:
    if locale == "en-US":
        parts = [f"{label} is {value!r}" for label, value in items]
        return f"Just to confirm, {_join_labels(parts, locale)}. Is that right?"
    if locale == "zh-CN":
        parts = [f"{label}是“{value}”" for label, value in items]
        return f"请确认：{'、'.join(parts)}，对吗？"
    if locale == "pt-BR":
        parts = [f"{label} é {value!r}" for label, value in items]
        return f"Só para confirmar: {_join_labels(parts, locale)}. Está correto?"
    parts = [f"{label}は「{value}」" for label, value in items]
    return f"{'、'.join(parts)}という理解でよろしいですか？"
