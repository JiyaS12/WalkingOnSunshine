"""Constrained interpretation and optional bounded conversational acknowledgment."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from typing import Iterable, Protocol

from .conversation_policy import INTERPRETER_INSTRUCTIONS
from .conversation_bridge import validated_bridge
from .models import SurveyQuestion, normalize_answer

INTENTS = (
    "answer", "select", "confirm", "adjust_down", "adjust_up", "clarify", "reject", "repeat", "pause", "resume", "stop",
    "medical_question", "off_topic",
)
MAX_TRANSCRIPT_CHARS = 12000
SEVERITY_ORDER = ("none", "mild", "moderate", "severe", "extreme")

# Everyday ways of naming a point on the severity scale. Each is a whole
# answer once lead-ins ("I'd say", "like") and the question's own noun ("hip
# pain", "difficulty") are set aside. Words of doubt ("maybe", "I think") are
# not lead-ins: they leave the reply to the model and a confirmation.
_LEAD_INS = re.compile(
    r"\b(?:i'?d say|i would say|i mean|i'?d go with|it'?s been|it'?s|it was|"
    r"i'?ve had|i had|i have|i experienced|i'?ve experienced|there was|there'?s been|"
    r"about|around|like|um+|uh+|honestly|really|just|actually|definitely|"
    r"so|well|yeah|yes|oh|the|this|past|week|for me|on stairs|with that)\b"
)
_SCALE_NOUN = re.compile(
    r"\b(?:hip pain|pain|difficulty|difficulties|discomfort|trouble|problems?|issues?|"
    r"soreness|aching|ache|painful|amount|level)\b"
)
_PARAPHRASES = {
    "none": {
        "no x", "no x at all", "not any x", "none at all", "none whatsoever", "not at all",
        "nothing", "nothing at all", "zero x", "zero", "no x whatsoever", "none x",
        "not any", "didn't have any x", "didn't have any", "haven't had any x",
    },
    "mild": {
        "a little", "a little x", "a little bit", "a little bit x", "a bit", "a bit x",
        "slight", "slight x", "slightly", "minor", "minor x", "not much", "not much x",
        "not too much", "not too much x", "very little", "very little x", "barely any",
        "barely any x", "hardly any", "hardly any x", "a tiny bit", "mildly", "small x",
        "only a little", "only a little x", "not a lot", "not a lot x",
    },
    "moderate": {
        "moderately", "medium", "medium x", "moderate x", "moderate amount x",
        "somewhat", "some", "some x", "a fair amount", "a fair amount x", "in the middle",
        "middle of the road", "average", "so so", "a fair bit", "a fair bit x",
    },
    "severe": {
        "a lot", "a lot x", "severely", "bad", "bad x", "very bad", "very bad x", "pretty bad",
        "pretty bad x", "really bad", "really bad x", "quite a lot", "quite a lot x",
        "very painful", "a great deal", "a great deal x", "significant x", "a whole lot",
        "a whole lot x", "serious x", "severe x",
    },
    "extreme": {
        "extremely", "extremely x", "extreme x", "unbearable", "unbearable x", "excruciating", "excruciating x",
        "the worst", "worst x", "terrible", "terrible x", "horrible", "horrible x", "awful",
        "awful x", "agony", "impossible", "couldn't do it", "couldn't do it at all", "can't do it",
        "can't do it at all", "i couldn't do it", "i couldn't do it at all", "i can't do it",
        "i can't do it at all", "as bad as it gets",
    },
}


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


def paraphrased_answer(transcript: str, options: Iterable[str]) -> str | None:
    """Read a plain-language severity ("no difficulty", "a lot of pain") as its label.

    Only the fixed table above matches, and only when it accounts for the
    whole utterance; "no pain but a lot of stiffness" or "a lot, I think
    it's getting worse" fall through to the model.
    """

    cleaned = _SCALE_NOUN.sub("x", clean_utterance(transcript))
    cleaned = " ".join(_LEAD_INS.sub(" ", cleaned).split())
    for _ in range(2):
        cleaned = re.sub(r"\b(?:an?|of|of the|x) x\b", "x", cleaned)
    cleaned = re.sub(r"^an? (?=(?:moderate|severe|extreme|slight|minor|small|medium) )", "", cleaned)
    if not cleaned:
        return None
    label = normalize_answer(cleaned, options)
    if label:
        return label
    for label, phrases in _PARAPHRASES.items():
        if cleaned in phrases and label in options:
            return label
    return None


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
        cleaned = clean_utterance(transcript)
        value = normalize_answer(cleaned, question.answer_options)
        if value:
            return Interpretation("select", value, transcript)
        if pending_value is None:
            # A plain phrase for a point on the scale is the patient's own
            # choice; while a proposal is pending, "not much" or "a bit less"
            # are about that proposal and are read there instead.
            value = paraphrased_answer(transcript, question.answer_options)
            if value:
                return Interpretation("select", value, transcript)
        return Interpretation()


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
