"""Deterministic generic intake, collected independently from condition options."""

import re
from dataclasses import dataclass

from .answer_interpreter import clean_utterance, control_intent
from .survey_engine import MAX_CLARIFICATIONS, NO, YES

Value = int | bool | str | list[str] | None
UNKNOWN = {"unknown", "i don't know", "i do not know", "not sure", "prefer not to say"}
NUMBERS = {name: index for index, name in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
)}


@dataclass(frozen=True)
class IntakeQuestion:
    key: str
    prompt: str
    kind: str


QUESTIONS = (
    IntakeQuestion("pain_scale", "What is your pain from 1 to 10? You can also say unknown.", "pain"),
    IntakeQuestion("falls_last_6_months", "How many falls have you had in the last six months? Say a count or unknown.", "count"),
    IntakeQuestion("injured", "Were you injured in a fall in the last six months? Say yes, no, or unknown.", "boolean"),
    IntakeQuestion("last_fall_description", "Please describe your last fall or injury, or say none or unknown.", "text"),
    IntakeQuestion("dizziness", "Have you experienced dizziness? Say yes, no, or unknown.", "boolean"),
    IntakeQuestion("dizziness_notes", "What would you like to note about dizziness? You can say none or unknown.", "text"),
    IntakeQuestion("primary_complaints", "What are your main complaints? You can say none or unknown. Keep each complaint short.", "complaints"),
)


class GenericIntake:
    def __init__(self):
        self.index = 0
        self.values: dict[str, Value] = {}
        self.pending: Value = None
        self.has_pending = False
        self.paused = False
        self.state = "asking"
        self.failures = 0
        self.confirmation = ""

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
            return "I could not confirm this answer. We will stop without submitting an incomplete intake."
        return f"I could not confirm that. {self.prompt()}"

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
                self.values[QUESTIONS[self.index].key] = self.pending
                self.has_pending = False
                self.pending = None
                self.index += 1
                self.failures = 0
                if self.index == len(QUESTIONS):
                    self.state = "complete"
                    return "Your generic intake answers are confirmed."
                return self.prompt()
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
        valid, value = parse_value(question.kind, text)
        if not valid:
            return self.retry()
        self.pending = value
        self.has_pending = True
        display = "unknown" if value is None else (
            "yes" if value is True else "no" if value is False else
            "; ".join(value) if isinstance(value, list) else str(value)
        )
        if question.kind in {"text", "complaints"} and cleaned == "none":
            display = "none reported"
        self.confirmation = f'I heard "{display}". Is that correct? Say yes or no.'
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


def parse_value(kind: str, text: str) -> tuple[bool, Value]:
    cleaned = clean_utterance(text)
    if cleaned in UNKNOWN:
        return True, None
    if kind in {"text", "complaints"} and cleaned == "none":
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
        complaints = [part.strip() for part in stripped.split(";")]
        return (True, complaints) if 1 <= len(complaints) <= 10 and all(
            0 < len(part) <= 120 for part in complaints
        ) else (False, None)
    return (True, stripped) if len(stripped) <= 500 else (False, None)
