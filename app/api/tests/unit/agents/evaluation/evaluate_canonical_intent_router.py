"""Run the real Bedrock router against the human-labelled intent set.

This is an evaluation harness, not an application entry point.  It calls the
same ``resolve_canonical_intent`` policy used by the API, but does not create a
record, write interview state, call RAG, or generate a question.  Full
Structured comparison is also provider-only and therefore has no persistence
side effects.

Example (from ``app/api``):

    PYTHONPATH=src .venv/bin/python \
      tests/unit/agents/evaluation/evaluate_canonical_intent_router.py

The caller may source the repository's environment before invoking this
script.  This module never prints environment values or credentials.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import median
from time import monotonic
from typing import Any, Iterable, Mapping

from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProviderError,
)
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.services.conversation_policy import (
    BedrockCanonicalIntentProvider,
    resolve_canonical_action,
    resolve_canonical_intent,
)

try:
    from tests.unit.agents.evaluation.canonical_intent_cases import (
        CASES,
        CanonicalIntentCase,
        build_case_context,
    )
except ModuleNotFoundError:  # Direct execution puts this directory on sys.path.
    from canonical_intent_cases import CASES, CanonicalIntentCase, build_case_context


_LOG_VALUE = re.compile(r"(?P<key>[a-zA-Z_]+)=(?P<value>[^ ]+)")
_ALL_INTENTS = (
    "ANSWER",
    "CONFIRMATION",
    "REJECTION",
    "CLARIFICATION_REQUEST",
    "QUESTION_TO_ASSISTANT",
    "CORRECTION",
    "HESITATION",
    "BACKCHANNEL",
    "IRRELEVANT",
    "CONVERSATION_REQUEST",
    "OTHER",
)
_IMPORTANT_CASES = {
    "大丈夫です。",
    "違います。",
    "担当領域ってどの範囲のこと？",
    "え、すでに回答しているけど。",
    "だから回答してるって。",
    "おーい。",
    "こんにちは。",
    "いや、あなたが質問しないからなんもわかんないんだけど。",
    "関わった相手ってどこの範囲での話をしている？",
}


class ProviderLogCapture(logging.Handler):
    """Capture sanitized provider telemetry already emitted by the adapter."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.request: dict[str, str] = {}
        self.usage: dict[str, str] = {}
        self.router_failures: list[str] = []

    def reset(self) -> None:
        self.request = {}
        self.usage = {}
        self.router_failures = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("structured_llm_request"):
            self.request = _parse_log_values(message)
        elif message.startswith("structured_llm_usage"):
            self.usage = _parse_log_values(message)
        elif message.startswith("conversation_router_failed"):
            self.router_failures.append(message)


def _parse_log_values(message: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in _LOG_VALUE.finditer(message)}


def _attach_capture(capture: ProviderLogCapture) -> list[logging.Logger]:
    logger_names = (
        "ai_interviewer_api.agents.interview_knowledge.provider",
        "ai_interviewer_api.services.conversation_policy",
    )
    loggers: list[logging.Logger] = []
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(capture)
        loggers.append(logger)
    return loggers


def _detach_capture(capture: ProviderLogCapture, loggers: Iterable[logging.Logger]) -> None:
    for logger in loggers:
        logger.removeHandler(capture)


def _as_optional_int(value: str | None) -> int | None:
    if value in {None, "", "None", "null"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _usage_snapshot(capture: ProviderLogCapture) -> dict[str, Any]:
    request = capture.request
    usage = capture.usage
    return {
        "inputTokens": _as_optional_int(usage.get("input_tokens")),
        "outputTokens": _as_optional_int(usage.get("output_tokens")),
        "totalTokens": _as_optional_int(usage.get("total_tokens")),
        "estimatedInputTokens": _as_optional_int(request.get("estimated_input_tokens")),
        "usageAvailable": bool(usage),
    }


def _question_definition(
    *,
    target_id: str = "field-responsibility",
    title: str = "担当領域",
    description: str = "現在担当している領域や役割",
    original_question: str = "現在の担当領域を教えてください。",
) -> dict[str, Any]:
    return {
        "targetId": target_id,
        "title": title,
        "description": description,
        "originalQuestion": original_question,
        "requiredItems": [
            {
                "itemId": target_id,
                "label": title,
                "description": description,
            }
        ],
        "optionalItems": [],
        "missingItems": ["担当領域"],
    }


def _full_interpreter_context(case: CanonicalIntentCase) -> dict[str, Any]:
    narrow = build_case_context(case)
    question = dict(narrow["current_question"])
    target = dict(narrow["current_target"])
    latest_id = f"evaluation-{case.case_id}"
    recent = [dict(message) for message in narrow["recent_conversation"]]
    conversation = [
        {
            "id": f"evaluation-context-{case.case_id}",
            "role": message["role"],
            "content": message["content"],
            "rawTranscript": None,
            "questionId": question["questionId"],
            "answerToQuestionId": None,
            "isActualUtterance": True,
        }
        for message in recent
    ]
    conversation.append(
        {
            "id": latest_id,
            "role": "user",
            "content": case.utterance,
            "rawTranscript": case.utterance,
            "questionId": question["questionId"],
            "answerToQuestionId": question["questionId"],
            "isActualUtterance": True,
        }
    )
    current_question = {
        **question,
        "questionDefinition": _question_definition(
            target_id=str(target.get("targetId") or "evaluation-target"),
            title=str(target.get("label") or "現在の質問"),
            description=str(target.get("label") or "現在の質問"),
            original_question=str(question.get("text") or ""),
        ),
    }
    question_definition = current_question["questionDefinition"]
    return {
        "profile": "fixed_form",
        "knowledge": {
            "id": "evaluation-knowledge",
            "name": "Canonical Intent evaluation",
            "description": "Provider-only intent evaluation",
            "purpose": "Evaluate dialogue act classification",
        },
        "record": {
            "id": "evaluation-record",
            "title": "Canonical Intent evaluation",
        },
        "fields": [
            {
                "id": target["targetId"],
                "name": target["label"],
                "description": question_definition["description"],
                "required": True,
                "inputType": "long_text",
                "questionPlan": {
                    "requiredItems": question_definition["requiredItems"],
                    "optionalItems": [],
                },
            }
        ],
        "currentQuestion": current_question,
        "interviewState": {
            "interviewProfile": "fixed_form",
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": target,
            "pendingTranscriptConfirmation": bool(narrow["pending_confirmation"]),
            "lastProcessedUserMessageId": None,
            "fieldStates": {},
            "requirementStates": {},
        },
        "latestUtterance": {
            "messageId": latest_id,
            "rawTranscript": case.utterance,
            "normalizedTranscript": case.utterance,
            "correctionStatus": "NONE",
            "correctionCandidates": [],
            "sttConfidence": 1.0,
        },
        "conversation": conversation,
    }


def _run_router_case(
    case: CanonicalIntentCase,
    router: BedrockCanonicalIntentProvider,
    capture: ProviderLogCapture,
) -> dict[str, Any]:
    context = build_case_context(case)
    capture.reset()
    decision_started_at = monotonic()
    decision = resolve_canonical_intent(
        utterance=case.utterance,
        current_question=context["current_question"],
        current_target=context["current_target"],
        pending_confirmation=context["pending_confirmation"],
        recent_conversation=context["recent_conversation"],
        provider=router,
    )
    measured_latency_ms = round((monotonic() - decision_started_at) * 1000, 1)
    actual = decision.dialogueAct
    action = resolve_canonical_action(
        actual,
        pending_confirmation=context["pending_confirmation"],
        current_target=context["current_target"],
    )
    return {
        "case": asdict(case),
        "routerIntent": actual,
        "canonicalAction": action,
        "correct": actual == case.expected_intent,
        "routerLatencyMs": decision.latency_ms,
        "measuredLatencyMs": measured_latency_ms,
        "usage": _usage_snapshot(capture),
        "routerError": capture.router_failures[-1] if capture.router_failures else None,
    }


def _run_structured_case(
    case: CanonicalIntentCase,
    provider: BedrockResponsesStructuredProvider,
    capture: ProviderLogCapture,
) -> dict[str, Any]:
    capture.reset()
    started_at = monotonic()
    try:
        output = provider.interpret(
            profile="fixed_form",
            context=_full_interpreter_context(case),
            reasoning_effort=settings.structured_interview_reasoning_effort,
        )
        return {
            "structuredDialogueAct": output.dialogueAct,
            "structuredLatencyMs": round((monotonic() - started_at) * 1000, 1),
            "structuredUsage": _usage_snapshot(capture),
            "structuredError": None,
        }
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        return {
            "structuredDialogueAct": None,
            "structuredLatencyMs": round((monotonic() - started_at) * 1000, 1),
            "structuredUsage": _usage_snapshot(capture),
            "structuredError": exc.__class__.__name__,
        }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 1)


def _latency_summary(results: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = [float(result[key]) for result in results if result.get(key) is not None]
    return {
        "min": round(min(values), 1) if values else None,
        "median": round(float(median(values)), 1) if values else None,
        "p90": _percentile(values, 0.9),
        "max": round(max(values), 1) if values else None,
    }


def _number_summary(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p90": None, "max": None}
    numeric = [float(value) for value in values]
    return {
        "count": len(values),
        "min": min(values),
        "median": round(float(median(numeric)), 1),
        "p90": _percentile(numeric, 0.9),
        "max": max(values),
    }


def _usage_summary(results: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values: dict[str, list[int]] = {
        "inputTokens": [],
        "outputTokens": [],
        "totalTokens": [],
        "estimatedInputTokens": [],
    }
    for result in results:
        usage = result.get(key) or {}
        if not isinstance(usage, Mapping):
            continue
        for name in values:
            value = usage.get(name)
            if isinstance(value, int):
                values[name].append(value)
    return {name: _number_summary(items) for name, items in values.items()}


def _classification_metrics(
    results: list[dict[str, Any]],
    *,
    actual_key: str,
) -> dict[str, Any]:
    expected = [str(result["case"]["expected_intent"]) for result in results]
    actual = [str(result.get(actual_key) or "ERROR") for result in results]
    labels = list(_ALL_INTENTS)
    per_intent: dict[str, dict[str, float | int]] = {}
    for label in labels:
        tp = sum(item == label and truth == label for truth, item in zip(expected, actual))
        fp = sum(item == label and truth != label for truth, item in zip(expected, actual))
        fn = sum(item != label and truth == label for truth, item in zip(expected, actual))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_intent[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    non_answer_total = sum(truth != "ANSWER" for truth in expected)
    non_answer_to_answer = sum(
        truth != "ANSWER" and item == "ANSWER"
        for truth, item in zip(expected, actual)
    )
    return {
        "accuracy": round(sum(truth == item for truth, item in zip(expected, actual)) / len(results), 4),
        "perIntent": per_intent,
        "nonAnswerToAnswer": {
            "count": non_answer_to_answer,
            "totalNonAnswer": non_answer_total,
            "rate": round(non_answer_to_answer / non_answer_total, 4)
            if non_answer_total
            else 0.0,
        },
    }


def _print_report(report: Mapping[str, Any]) -> None:
    router_metrics = report["routerMetrics"]
    print("Canonical Intent Router evaluation")
    print(f"cases={report['metadata']['caseCount']}")
    print(f"router_accuracy={router_metrics['accuracy']}")
    print(
        "non_answer_to_answer_rate="
        f"{router_metrics['nonAnswerToAnswer']['rate']} "
        f"({router_metrics['nonAnswerToAnswer']['count']}/"
        f"{router_metrics['nonAnswerToAnswer']['totalNonAnswer']})"
    )
    print(f"router_latency={report['routerLatency']}")
    print(f"router_usage={report['routerUsage']}")
    if report.get("structuredMetrics") is not None:
        print(f"structured_accuracy={report['structuredMetrics']['accuracy']}")
        print(f"structured_latency={report['structuredLatency']}")
        print(f"structured_usage={report['structuredUsage']}")
    print("important_cases:")
    for result in report["results"]:
        case = result["case"]
        if case["utterance"] not in _IMPORTANT_CASES:
            continue
        print(
            f"- {case['case_id']}: expected={case['expected_intent']} "
            f"router={result.get('routerIntent')} action={result.get('canonicalAction')} "
            f"structured={result.get('structuredDialogueAct')}"
        )


def run(*, output_path: Path | None, skip_structured: bool) -> int:
    router_provider = BedrockResponsesStructuredProvider(
        model_id=settings.structured_interview_fast_model_id,
    )
    router = BedrockCanonicalIntentProvider(provider=router_provider)
    structured_provider = (
        None
        if skip_structured
        else BedrockResponsesStructuredProvider(
            model_id=settings.structured_interview_model_id,
        )
    )
    capture = ProviderLogCapture()
    loggers = _attach_capture(capture)
    try:
        results: list[dict[str, Any]] = []
        for case in CASES:
            result = _run_router_case(case, router, capture)
            if structured_provider is not None:
                result.update(_run_structured_case(case, structured_provider, capture))
            else:
                result.update(
                    {
                        "structuredDialogueAct": None,
                        "structuredLatencyMs": None,
                        "structuredUsage": None,
                        "structuredError": "skipped",
                    }
                )
            results.append(result)
    finally:
        _detach_capture(capture, loggers)

    report: dict[str, Any] = {
        "metadata": {
            "caseCount": len(CASES),
            "routerModel": settings.structured_interview_fast_model_id,
            "routerReasoningEffort": settings.structured_interview_fast_reasoning_effort,
            "structuredModel": settings.structured_interview_model_id,
            "structuredReasoningEffort": settings.structured_interview_reasoning_effort,
            "structuredComparisonSkipped": skip_structured,
        },
        "routerMetrics": _classification_metrics(results, actual_key="routerIntent"),
        "routerLatency": _latency_summary(results, "routerLatencyMs"),
        "routerUsage": _usage_summary(results, "usage"),
        "structuredMetrics": None
        if skip_structured
        else _classification_metrics(results, actual_key="structuredDialogueAct"),
        "structuredLatency": None
        if skip_structured
        else _latency_summary(results, "structuredLatencyMs"),
        "structuredUsage": None
        if skip_structured
        else _usage_summary(results, "structuredUsage"),
        "results": results,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"result_file={output_path}")
    _print_report(report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/kikiori-canonical-intent-evaluation.json"),
        help="sanitized JSON result path (default: /tmp/...)",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="evaluate only the Canonical Router",
    )
    args = parser.parse_args()
    try:
        return run(output_path=args.output, skip_structured=args.skip_structured)
    except Exception as exc:  # noqa: BLE001 - CLI reports type only, never secrets
        print(f"evaluation_failed error_type={exc.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
