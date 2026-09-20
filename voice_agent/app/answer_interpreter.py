"""Constrained interpretation and optional bounded conversational acknowledgment."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from typing import Protocol

from .conversation_policy import INTERPRETER_INSTRUCTIONS
from .conversation_bridge import validated_bridge
from .models import SurveyQuestion, normalize_answer

INTENTS = (
    "answer", "select", "confirm", "adjust_down", "adjust_up", "clarify", "reject", "repeat", "pause", "resume", "stop",
    "medical_question", "off_topic",
)
MAX_TRANSCRIPT_CHARS = 12000
SEVERITY_ORDER = ("none", "mild", "moderate", "severe", "extreme")


@dataclass(frozen=True)
class Interpretation:
    intent: str = "clarify"
    value: str | None = None
    evidence: str | None = None
    acknowledgment: str | None = None


class AnswerInterpreter(Protocol):
    def interpret(
        self, transcript: str, question: SurveyQuestion, pending_value: str | None = None,
    ) -> Interpretation: ...


def clean_utterance(transcript: str) -> str:
    # Speech recognition may insert commas or sentence breaks into a single
    # affirmation. Keep all words so 'yes, but no' cannot become 'yes'.
    return " ".join(re.sub(r"[,.;:!?]", " ", transcript.casefold().replace("’", "'")).split())


def control_intent(transcript: str) -> str | None:
    """Whole-utterance controls also work without an LLM or during an outage."""
    cleaned = clean_utterance(transcript)
    commands = {
        "stop": {"stop", "stop please", "please stop", "stop the survey", "please stop the survey", "end the call",
                 "i want to stop", "i don't want to continue", "i don't want to do this"},
        "pause": {"pause", "please pause", "wait", "hold on", "give me a moment",
                  "i need a break", "i need more time"},
        "resume": {"resume", "continue", "i'm ready", "let's continue"},
        "repeat": {"repeat", "please repeat", "say that again", "repeat the question",
                   "please repeat the question", "what are the choices", "what are the options",
                   "speak slowly", "please speak slowly", "i don't understand the question"},
    }
    # A clear closing stop clause outranks a severity label earlier in the turn.
    # Restrict this fast path to explicit clauses, not a substring like 'nonstop'.
    clauses = re.split(r"[,;.!?]|\b(?:but|and)\b", transcript.casefold().replace("’", "'"))
    clauses = [clean_utterance(clause) for clause in clauses if clean_utterance(clause)]
    if clauses and clauses[-1] in commands["stop"]:
        return "stop"
    return next((intent for intent, phrases in commands.items() if cleaned in phrases), None)


def is_unscaled_number(transcript: str) -> bool:
    """A bare number has no declared scale and must not become a severity label."""
    return re.fullmatch(
        r"(?:a |about |around )?(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten)",
        transcript.strip().casefold().rstrip(".!?"),
    ) is not None


def validated_interpretation(result, transcript: str, question: SurveyQuestion, pending_value: str | None = None) -> Interpretation:
    """Reject invalid adapter output even if the provider claimed schema adherence."""
    if not isinstance(result, Interpretation) or result.intent not in INTENTS:
        return Interpretation()
    if result.intent in {"answer", "select", "confirm"}:
        if (
            not isinstance(result.value, str)
            or result.value not in question.answer_options
            or not isinstance(result.evidence, str)
            or not result.evidence.strip()
            or result.evidence not in transcript
        ):
            return Interpretation()
        if result.intent == "confirm" and (
            pending_value not in question.answer_options
            or result.value != pending_value
            or result.evidence.strip() != transcript.strip()
        ):
            return Interpretation()
        if result.intent == "select" and (
            result.evidence.strip() != transcript.strip()
            or not re.search(rf"\b{re.escape(result.value)}\b", transcript, re.IGNORECASE)
        ):
            return Interpretation()
    elif result.intent in {"adjust_down", "adjust_up"}:
        if (
            tuple(question.answer_options) != SEVERITY_ORDER
            or pending_value not in question.answer_options
            or result.value is not None
            or result.evidence != transcript
            or not transcript.strip()
        ):
            return Interpretation()
        # The model identifies context/direction, but cannot turn an unspecified
        # large rejection into an adjacent step. 'Maybe a little less' is fine;
        # 'much less, I cannot choose' must discard the old proposal and ask.
        cleaned = clean_utterance(transcript)
        small_change = re.search(
            r"\b(?:a little|a bit|a touch|a tad|a shade|slightly|just below|just above|"
            r"one (?:step|notch|level|category)|next (?:lower|higher)|small(?:er)? (?:amount|step))\b",
            cleaned,
        )
        incompatible = re.search(r"\b(?:much|far|a lot|cannot choose|can't choose|not sure)\b", cleaned)
        if not small_change or incompatible:
            return Interpretation("reject", acknowledgment=validated_bridge(result.acknowledgment))
        index = question.answer_options.index(pending_value)
        target = index + (-1 if result.intent == "adjust_down" else 1)
        if not 0 <= target < len(question.answer_options):
            # An impossible adjustment must not leave the rejected endpoint
            # pending, where a later yes could accidentally accept it.
            return Interpretation("reject")
    elif result.value is not None or result.evidence is not None:
        return Interpretation()
    if result.intent == "reject" and pending_value is None:
        # Nothing was proposed, so this is an unclear answer, not a correction.
        result = replace(result, intent="clarify")
    return replace(result, acknowledgment=validated_bridge(result.acknowledgment))


class ExactAnswerInterpreter:
    """Offline fallback: never guess the meaning of free-form language."""

    def interpret(self, transcript, question, pending_value=None) -> Interpretation:
        intent = control_intent(transcript)
        if intent:
            return Interpretation(intent)
        value = normalize_answer(clean_utterance(transcript), question.answer_options)
        return Interpretation("select", value, transcript) if value else Interpretation()


class OpenAIAnswerInterpreter:
    def __init__(self, client, model: str = "gpt-4.1-mini"):
        self.client = client
        self.model = model

    def interpret(self, transcript, question, pending_value=None) -> Interpretation:
        if not transcript.strip() or len(transcript) > MAX_TRANSCRIPT_CHARS or is_unscaled_number(transcript):
            return Interpretation()
        direct = ExactAnswerInterpreter().interpret(transcript, question, pending_value)
        if direct.intent != "clarify":
            return direct
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": [
                    intent for intent in INTENTS
                    if intent not in {"confirm", "adjust_down", "adjust_up"} or pending_value is not None
                ]},
                "value": {"type": ["string", "null"], "enum": [*question.answer_options, None]},
                "evidence": {"type": ["string", "null"]},
                "acknowledgment": {"type": ["string", "null"]},
            },
            "required": ["intent", "value", "evidence", "acknowledgment"],
            "additionalProperties": False,
        }
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": INTERPRETER_INSTRUCTIONS},
                    {"role": "user", "content": json.dumps({
                        "question": {"id": question.id, "prompt": question.prompt,
                                     "answer_options": question.answer_options},
                        "pending_value": pending_value,
                        "transcript": transcript,
                    })},
                ],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "survey_interpretation", "strict": True, "schema": schema,
                }},
                store=False,
            )
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                return Interpretation()
            payload = json.loads(choice.message.content)
            if not isinstance(payload, dict) or set(payload) != {"intent", "value", "evidence", "acknowledgment"}:
                return Interpretation()
            return validated_interpretation(Interpretation(**payload), transcript, question, pending_value)
        except Exception:
            # Provider failures, refusals, and invalid output never become speech or answers.
            return Interpretation()


def build_answer_interpreter() -> AnswerInterpreter:
    mode = os.getenv("SURVEY_EXTRACTOR", "exact").strip().casefold()
    if mode == "exact":
        return ExactAnswerInterpreter()
    if mode != "openai":
        raise ValueError("SURVEY_EXTRACTOR must be 'exact' or 'openai'.")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        # The local demo remains usable with just Deepgram configured.
        return ExactAnswerInterpreter()
    from openai import OpenAI

    return OpenAIAnswerInterpreter(
        OpenAI(timeout=15.0, max_retries=0),
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    )
