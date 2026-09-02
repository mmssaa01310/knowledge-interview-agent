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

from ai_interviewer_api.core.interview_locale import InterviewLocale


_MAX_SPOKEN_VALUE_LENGTH = 64
_MAX_CANDIDATE_LENGTH = 120


def build_transcribe_polly_transcript_feedback(
    result: Mapping[str, Any],
    turn: Mapping[str, Any],
    *,
    field_labels: Mapping[str, str],
    locale: InterviewLocale,
    force_retry: bool = False,
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
    correction_candidates = _unique_texts(assessment.get("correctionCandidates"))
    base_state = _as_mapping(turn.get("baseInterviewState"))
    base_pending = base_state.get("pendingTranscriptConfirmation")
    rejected_transcript_correction = (
        _clean_text(output.get("dialogueAct")) == "REJECTION"
        and isinstance(base_pending, Mapping)
    )

    if status == "CORRECTED":
        normalized = _correction_candidate(state, assessment)
        has_confirmation_target = _has_confirmation_target(state)
        if (
            has_confirmation_target
            and normalized
            and len(correction_candidates) <= 1
            and len(normalized) <= _MAX_CANDIDATE_LENGTH
        ):
            labels = _confirmation_labels(state, question, field_labels, locale)
            return _correction_confirmation_message(
                labels=labels,
                candidate=normalized,
                locale=locale,
            )
        # A malformed or non-unique correction must not be spoken as if it
        # were a reliable candidate. Use targeted retry wording instead.
        status = "UNCERTAIN"

    if status != "UNCERTAIN" and not rejected_transcript_correction and not force_retry:
        return None

    latest_message_ids = _latest_message_ids(state, turn)
    reliable_updates = (
        {}
        if correction_candidates
        else _reliable_field_updates(
            output,
            latest_message_ids=latest_message_ids,
            field_labels=field_labels,
        )
    )
    unclear_labels = _unclear_labels(
        state,
        question,
        field_labels,
        known_field_ids=set(reliable_updates),
        locale=locale,
    )
    understood = _understood_phrase(reliable_updates, field_labels, locale)

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
    if understood:
        if locale == "en-US":
            return f"I understood {understood}. Please repeat the part about {subject}."
        if locale == "zh-CN":
            return f"我听懂了{understood}。请再说明一下{subject}。"
        if locale == "pt-BR":
            return f"Entendi {understood}. Por favor, repita a parte sobre {subject}."
        return f"{understood}ということは分かりました。{subject}について、もう一度お願いします。"

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


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


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
            or _clean_text(update.get("answerResolution")) != "AUTO_CONFIRM"
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


def _has_confirmation_target(
    state: Mapping[str, Any],
) -> bool:
    pending = _as_mapping(state.get("pendingTranscriptConfirmation"))
    target_refs = pending.get("targetRefs")
    return isinstance(target_refs, list) and bool(target_refs)


def _correction_candidate(
    state: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> str:
    pending = _as_mapping(state.get("pendingTranscriptConfirmation"))
    target_refs = pending.get("targetRefs")
    candidates: list[str] = []
    if isinstance(target_refs, list):
        field_states = state.get("fieldStates")
        requirement_states = state.get("requirementStates")
        for item in target_refs:
            target = _as_mapping(item)
            target_id = _clean_text(target.get("targetId"))
            target_type = _clean_text(target.get("targetType"))
            if target_type == "field" and isinstance(field_states, Mapping):
                target_state = _as_mapping(field_states.get(target_id))
                value = _clean_text(target_state.get("candidateAnswer"))
            elif target_type in {"requirement", "process"} and isinstance(
                requirement_states, Mapping
            ):
                target_state = _as_mapping(requirement_states.get(target_id))
                value = _clean_text(target_state.get("candidateValue"))
            else:
                value = ""
            if value and value not in candidates:
                candidates.append(value)
    if candidates:
        concise_candidates = [
            concise
            for value in candidates
            if (concise := _display_value(value))
        ]
        concise_candidate = "、".join(concise_candidates)
        if concise_candidate and len(concise_candidate) <= _MAX_CANDIDATE_LENGTH:
            return concise_candidate
    return _clean_text(assessment.get("normalizedTranscript"))


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
    *,
    labels: list[str],
    candidate: str,
    locale: InterviewLocale,
) -> str:
    subject = _join_labels(labels, locale) if labels else ""
    if locale == "en-US":
        prefix = f"For {subject}, " if subject else ""
        return f"{prefix}did you mean {candidate!r}?"
    if locale == "zh-CN":
        prefix = f"关于{subject}，" if subject else ""
        return f"{prefix}您的意思是“{candidate}”吗？"
    if locale == "pt-BR":
        prefix = f"Sobre {subject}, " if subject else ""
        return f"{prefix}você quis dizer {candidate!r}?"
    prefix = f"{subject}は" if subject else "この内容は"
    return f"{prefix}「{candidate}」という理解でよろしいですか？"
