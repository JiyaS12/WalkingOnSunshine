"""Reuse synthesized audio for prompts the survey says on every call.

The question bank, the greeting and the walking guidance are fixed text, so
rendering them through Deepgram once per process (rather than once per call)
removes the synthesis round-trip from the caller's wait. Anything not cached
is synthesized as before; a cache miss is never an error.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable

from .. import conversation_policy as policy
from ..models import ConditionCategory
from ..question_loader import load_question_bank
from .call_session import _spoken as spoken
from .deepgram_tts import SpeechSynthesisError, synthesize_mulaw_async

logger = logging.getLogger(__name__)

# Roughly a minute of 8 kHz mu-law per entry cap keeps a runaway prompt out.
MAX_ENTRY_BYTES = 8000 * 60
MAX_ENTRIES = 512
WARM_CONCURRENCY = 4
# Uppercase policy strings that are read by the model, not spoken to a caller.
NOT_SPOKEN = frozenset({"INTERPRETER_INSTRUCTIONS", "PARAGRAPH"})

Synthesizer = Callable[[str, str, str], Awaitable[bytes]]
Chunker = Callable[[str], Iterable[str]]


class SpeechCache:
    """Bounded in-memory map from (model, text) to synthesized mu-law audio."""

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self.max_entries = max_entries
        self._audio: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._audio)

    def get(self, model: str, text: str) -> bytes | None:
        key = (model, text.strip())
        audio = self._audio.get(key)
        if audio is None:
            self.misses += 1
            return None
        self._audio.move_to_end(key)
        self.hits += 1
        return audio

    def put(self, model: str, text: str, audio: bytes) -> None:
        if not audio or len(audio) > MAX_ENTRY_BYTES:
            return
        key = (model, text.strip())
        self._audio[key] = audio
        self._audio.move_to_end(key)
        while len(self._audio) > self.max_entries:
            self._audio.popitem(last=False)

    async def warm(
        self,
        texts: Iterable[str],
        api_key: str,
        model: str,
        chunker: Chunker,
        synthesize: Synthesizer = synthesize_mulaw_async,
        concurrency: int = WARM_CONCURRENCY,
    ) -> int:
        """Synthesize every chunk of ``texts`` not already cached.

        Failures are logged and skipped: a prompt that could not be pre-rendered
        is simply rendered live when a call needs it. Returns how many chunks
        were added.
        """

        pending: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for chunk in chunker(text):
                chunk = chunk.strip()
                if not chunk or chunk in seen or (model, chunk) in self._audio:
                    continue
                seen.add(chunk)
                pending.append(chunk)
        if not pending:
            return 0
        gate = asyncio.Semaphore(max(1, concurrency))
        added = 0

        async def render(chunk: str) -> None:
            nonlocal added
            async with gate:
                try:
                    audio = await synthesize(chunk, api_key, model)
                except (SpeechSynthesisError, OSError) as error:
                    logger.warning("Prompt cache skipped %r: %s", chunk[:40], error)
                    return
                except Exception:
                    logger.exception("Prompt cache skipped %r", chunk[:40])
                    return
            before = len(self._audio)
            self.put(model, chunk, audio)
            added += len(self._audio) - before

        await asyncio.gather(*(render(chunk) for chunk in pending))
        logger.info("Prompt cache warmed %d/%d chunks for %s", added, len(pending), model)
        return added


def fixed_prompts() -> list[str]:
    """Every prompt whose wording does not depend on what the caller says."""

    prompts: list[str] = [
        value for name, value in vars(policy).items()
        if name.isupper() and isinstance(value, str) and value.strip()
        and name not in NOT_SPOKEN
    ]
    for category in ConditionCategory:
        questions = load_question_bank(category)
        total = len(questions)
        for index, question in enumerate(questions):
            if index == 0:
                prompts.append(policy.opening_text(question, total))
            prompts.append(policy.question_text(question, index, total))
            prompts.append(f"{policy.question_text(question, index, total)} {policy.options_text(question)}")
            prompts.append(policy.clarification_text(question, include_options=False))
            prompts.append(policy.clarification_text(question, include_options=True, attempt=1))
            for value in question.answer_options:
                prompts.append(policy.confirmation_text(question, value))
                prompts.append(policy.readback_text(question, value))
    return list(dict.fromkeys(spoken(prompt) for prompt in prompts))
