"""Generic intake, collected independently from condition options.

Plain answers ("about a five", "no falls", "yes I have") are read directly.
Anything hedged or that only a model can read is proposed back for a yes/no.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .answer_interpreter import MAX_TRANSCRIPT_CHARS, clean_utterance, control_intent
from .survey_engine import MAX_CLARIFICATIONS, NO, YES

Value = int | bool | str | list[str] | None
UNKNOWN = {"unknown", "i don't know", "i do not know", "not sure", "i'm not sure", "prefer not to say",
           "i'd rather not say", "don't know", "dunno", "no idea", "can't remember", "i can't remember"}
NUMBERS = {name: index for index, name in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
)}
_NUMBER_WORDS = {**NUMBERS, "once": 1, "twice": 2, "none": 0, "no": 0, "nothing": 0, "never": 0}
_HEDGES = re.compile(r"\b(?:maybe|perhaps|probably|i think|i guess|possibly|or|kind of|sort of|i suppose|ish)\b")
_FILLER = re.compile(
    r"\b(?:i'?d say|i would say|i'?d go with|i'?d put it at|it'?s|it is|about|around|like|um+|uh+|"
    r"honestly|really|just|so|well|yeah|oh|a|an|the|out of ten|out of 10|on the scale|right now|today|"
    r"my pain is|pain is|falls?|times?|i'?ve had|i had|i have had|i fell|i'?ve fallen)\b"
)
_STRONG_YES = re.compile(r"\b(?:yes|yeah|yep|yup|correct|that's right)\b")
_YES_WORDS = re.compile(r"\b(?:yes|yeah|yep|yup|i have|i did|i do|correct|that's right|i was|i am|i'm)\b")
_NO_WORDS = re.compile(r"\b(?:no|nope|nah|never|none|not|n't|haven't|didn't|wasn't|don't|isn't|aren't)\b")
_DECIMAL = re.compile(r"\b(?:point|decimal|half|quarter)\b|\d\s*[.,]\s*\d")
_NONE_TEXT = {"none", "nothing", "no", "nope", "nothing to note", "nothing really", "not really",
              "no complaints", "no notes", "nothing to add", "nothing else"}
ACKNOWLEDGMENTS = ("Got it.", "Okay.", "Thanks.")


@dataclass(frozen=True)
class IntakeQuestion:
    key: str
    prompt: str
    kind: str


QUESTIONS = (
    IntakeQuestion(
        "pain_scale",
        "On a scale from one to ten, how would you rate your pain right now? If you're not sure, just say so.",
        "pain",
    ),
    IntakeQuestion(
        "falls_last_6_months",
        "In the last six months, how many times have you fallen? None is fine to say.",
        "count",
    ),
    IntakeQuestion("injured", "Have you been hurt in a fall in the last six months?", "boolean"),
    IntakeQuestion(
        "last_fall_description",
        "Could you tell me a little about your most recent fall or injury? If there wasn't one, just say none.",
        "text",
    ),
    IntakeQuestion("dizziness", "Have you been feeling dizzy at all lately?", "boolean"),
    IntakeQuestion(
        "dizziness_notes",
        "Is there anything you'd like to tell your care team about the dizziness? You can say none.",
        "text",
    ),
    IntakeQuestion(
        "primary_complaints",
        "Lastly, what's bothering you the most these days? You can name a few things, or say none.",
        "complaints",
    ),
)


@dataclass(frozen=True)
class IntakeReading:
    valid: bool
    value: Value = None
    clear: bool = False


class IntakeInterpreter(Protocol):
    def interpret(self, question: IntakeQuestion, transcript: str) -> IntakeReading: ...


class GenericIntake:
    def __init__(self, interpreter: IntakeInterpreter | None = None):
        self.index = 0
        self.values: dict[str, Value] = {}
        self.pending: Value = None
        self.has_pending = False
        self.paused = False
        self.state = "asking"
        self.failures = 0
        self.confirmation = ""
        self.interpreter = interpreter or LenientIntakeInterpreter()
        self._acknowledged = 0

    @property
    def complete(self) -> bool:
        return self.state == "complete"

    def prompt(self) -> str:
        if self.has_pending:
            return self.confirmation
        return QUESTIONS[self.index].prompt

    def retry(self) -> str:
        self.failures += 1
        if self.failures >= MAX_CLARIFICATIONS:
            self.state = "needs_review"
            self.has_pending = False
            return "I'm sorry, I'm having trouble catching that. We'll leave it there for now, and someone from your care team will follow up."
        if self.failures == 1:
            return f"Sorry, I didn't quite catch that. {self.prompt()}"
        return f"Let me try that once more. {self.prompt()}"

    def _accept(self, value: Value) -> str:
        self.values[QUESTIONS[self.index].key] = value
        self.has_pending = False
        self.pending = None
        self.index += 1
        self.failures = 0
        if self.index == len(QUESTIONS):
            self.state = "complete"
            return "Thank you, that covers the general questions."
        ack = ACKNOWLEDGMENTS[self._acknowledged % len(ACKNOWLEDGMENTS)]
        self._acknowledged += 1
        return f"{ack} {self.prompt()}"

    def handle(self, text: str) -> str:
        if self.state in {"complete", "stopped", "needs_review"}:
            return "This intake has ended."
        command = control_intent(text)
        if command == "stop":
            self.state = "stopped"
            self.has_pending = False
            return "We will stop here."
        if command == "pause":
            self.paused = True
            return "Paused. Say resume or stop."
        if self.paused:
            if command != "resume":
                return "Paused. Say resume or stop."
            self.paused = False
            return self.prompt()
        if command in {"repeat", "resume"}:
            return self.prompt()
        cleaned = clean_utterance(text)
        if self.has_pending:
            if cleaned in YES:
                return self._accept(self.pending)
            self.has_pending = False
            self.pending = None
            if cleaned in NO:
                return self.retry()
            # A correction is a fresh proposal, never confirmation.
            self.failures += 1
            if self.failures >= MAX_CLARIFICATIONS:
                self.state = "needs_review"
                return "I could not confirm this answer. We will stop here."
        question = QUESTIONS[self.index]
        reading = self.interpreter.interpret(question, text)
        if not reading.valid:
            return self.retry()
        if reading.clear:
            return self._accept(reading.value)
        self.pending = reading.value
        self.has_pending = True
        self.confirmation = f'I heard "{display_value(question, reading.value)}". Is that correct? Say yes or no.'
        return self.confirmation

    def payload(self) -> dict[str, object]:
        if not self.complete:
            raise ValueError("All generic questions must be explicitly confirmed.")
        return {
            "pain_scale": self.values["pain_scale"],
            "fall_history": {key: self.values[key] for key in (
                "falls_last_6_months", "injured", "last_fall_description",
            )},
            "dizziness": self.values["dizziness"],
            "dizziness_notes": self.values["dizziness_notes"],
            "primary_complaints": self.values["primary_complaints"],
        }


def display_value(question: IntakeQuestion, value: Value) -> str:
    if value is None:
        return "unknown"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, list):
        return "; ".join(value) if value else "none reported"
    return str(value)


def parse_value(kind: str, text: str) -> tuple[bool, Value]:
    """Strict whole-utterance reading: a bare number, yes/no, none, or unknown."""
    cleaned = clean_utterance(text)
    if cleaned in UNKNOWN:
        return True, None
    if kind in {"text", "complaints"} and cleaned in _NONE_TEXT:
        return True, [] if kind == "complaints" else None
    if kind in {"pain", "count"}:
        value = NUMBERS.get(cleaned)
        if re.fullmatch(r"\d{1,6}", cleaned):
            value = int(cleaned)
        if value is None or (kind == "pain" and not 1 <= value <= 10):
            return False, None
        return True, value
    if kind == "boolean":
        if cleaned in YES:
            return True, True
        if cleaned in NO:
            return True, False
        return False, None
    stripped = text.strip()
    if not stripped or any(ord(char) < 32 for char in stripped):
        return False, None
    if kind == "complaints":
        complaints = [part.strip() for part in re.split(r";|,|\band\b", stripped)]
        complaints = [part for part in complaints if part]
        return (True, complaints) if 1 <= len(complaints) <= 10 and all(
            0 < len(part) <= 120 for part in complaints
        ) else (False, None)
    return (True, stripped) if len(stripped) <= 500 else (False, None)


def lenient_parse(kind: str, text: str) -> IntakeReading:
    """Read everyday phrasing deterministically; hedged or mixed replies are never clear."""
    valid, value = parse_value(kind, text)
    if valid:
        # Free text is only clear when it is an explicit "nothing to report"; anything
        # else is proposed back so an off-topic remark cannot become the answer.
        return IntakeReading(True, value, clear=kind not in {"text", "complaints"} or not value)
    cleaned = clean_utterance(text)
    if not cleaned or len(text) > MAX_TRANSCRIPT_CHARS:
        return IntakeReading(False)
    if kind in {"pain", "count"} and _DECIMAL.search(text.casefold()):
        return IntakeReading(False)
    if any(phrase in cleaned for phrase in UNKNOWN) and kind != "text" and kind != "complaints":
        return IntakeReading(True, None, clear=not _HEDGES.search(cleaned))
    hedged = bool(_HEDGES.search(cleaned))
    if kind in {"pain", "count"}:
        stripped = " ".join(_FILLER.sub(" ", cleaned).split())
        numbers = [int(token) if token.isdigit() else _NUMBER_WORDS[token]
                   for token in stripped.split() if token.isdigit() or token in _NUMBER_WORDS]
        if kind == "count" and not numbers and _NO_WORDS.search(cleaned) and not _YES_WORDS.search(cleaned):
            numbers = [0]
        if len(numbers) != 1:
            return IntakeReading(False)
        number = numbers[0]
        if kind == "pain" and not 1 <= number <= 10:
            return IntakeReading(False)
        return IntakeReading(True, number, clear=not hedged and len(stripped.split()) <= 3)
    if kind == "boolean":
        no = bool(_NO_WORDS.search(cleaned))
        if no and _STRONG_YES.search(cleaned):
            return IntakeReading(False)
        yes = bool(_YES_WORDS.search(cleaned)) and not no
        if yes == no:
            return IntakeReading(False)
        return IntakeReading(True, yes, clear=not hedged)
    return IntakeReading(False)


class LenientIntakeInterpreter:
    def interpret(self, question: IntakeQuestion, transcript: str) -> IntakeReading:
        return lenient_parse(question.kind, transcript)


INTAKE_INSTRUCTIONS = """
You read a patient's spoken reply to one intake question on an automated phone check-in.
Return only what the patient actually said; never guess or fill in missing information.
- For a pain rating, return an integer 1-10 if the patient named exactly one number.
- For a fall count, return a non-negative integer if the patient stated one; "no falls"/"never" is 0.
- For a yes/no question, return true or false only if the reply clearly means one of them.
- For a free-text question, return a short verbatim summary of what they said, or an empty string if they said there is nothing to note.
- For complaints, return a list of short phrases, one per complaint, or an empty list if none.
- Return "unknown": true if the patient said they don't know or would rather not say.
- If the reply is ambiguous, off-topic, or a question, return "unclear": true.
""".strip()


class OpenAIIntakeInterpreter:
    """Deterministic reading first; the model only reads what the rules could not, and never confirms."""

    def __init__(self, client, model: str = "gpt-4.1-mini"):
        self.client = client
        self.model = model

    def interpret(self, question: IntakeQuestion, transcript: str) -> IntakeReading:
        direct = lenient_parse(question.kind, transcript)
        if (direct.valid and direct.clear) or not transcript.strip() or len(transcript) > MAX_TRANSCRIPT_CHARS:
            return direct
        if direct.valid and question.kind not in {"text", "complaints"}:
            return direct
        model = self._read(question, transcript)
        if not model.valid and direct.valid:
            # The model saw a question or an off-topic remark: re-ask instead of proposing it.
            return IntakeReading(False)
        return model

    def _read(self, question: IntakeQuestion, transcript: str) -> IntakeReading:
        value_schema = {
            "pain": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
            "count": {"type": ["integer", "null"], "minimum": 0, "maximum": 999999},
            "boolean": {"type": ["boolean", "null"]},
            "text": {"type": ["string", "null"], "maxLength": 500},
            "complaints": {"type": ["array", "null"], "items": {"type": "string", "maxLength": 120}, "maxItems": 10},
        }[question.kind]
        schema = {
            "type": "object",
            "properties": {"value": value_schema, "unknown": {"type": "boolean"}, "unclear": {"type": "boolean"}},
            "required": ["value", "unknown", "unclear"],
            "additionalProperties": False,
        }
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": INTAKE_INSTRUCTIONS},
                    {"role": "user", "content": json.dumps({
                        "question": question.prompt, "kind": question.kind, "transcript": transcript,
                    })},
                ],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "intake_reading", "strict": True, "schema": schema,
                }},
                store=False,
            )
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                return IntakeReading(False)
            payload = json.loads(choice.message.content)
        except Exception:
            return IntakeReading(False)
        if not isinstance(payload, dict) or payload.get("unclear") is True:
            return IntakeReading(False)
        if payload.get("unknown") is True:
            return IntakeReading(True, None)
        value = payload.get("value")
        return _validated_model_value(question.kind, value)


def _validated_model_value(kind: str, value: object) -> IntakeReading:
    if kind == "pain":
        return IntakeReading(True, value) if isinstance(value, int) and 1 <= value <= 10 else IntakeReading(False)
    if kind == "count":
        return IntakeReading(True, value) if isinstance(value, int) and 0 <= value <= 999999 else IntakeReading(False)
    if kind == "boolean":
        return IntakeReading(True, value) if isinstance(value, bool) else IntakeReading(False)
    if kind == "text":
        if value is None or (isinstance(value, str) and not value.strip()):
            return IntakeReading(True, None)
        return IntakeReading(True, value.strip()) if isinstance(value, str) and len(value) <= 500 else IntakeReading(False)
    if isinstance(value, list) and len(value) <= 10 and all(isinstance(v, str) and 0 < len(v) <= 120 for v in value):
        return IntakeReading(True, [v.strip() for v in value])
    return IntakeReading(False)


def build_intake_interpreter() -> IntakeInterpreter:
    mode = os.getenv("SURVEY_EXTRACTOR", "exact").strip().casefold()
    if mode != "openai" or not os.getenv("OPENAI_API_KEY", "").strip():
        return LenientIntakeInterpreter()
    from openai import OpenAI

    return OpenAIIntakeInterpreter(OpenAI(timeout=15.0, max_retries=0), model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
