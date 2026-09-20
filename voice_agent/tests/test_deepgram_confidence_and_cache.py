"""Deepgram recognizer confidence read-back and the pre-synthesized prompt cache."""

from __future__ import annotations

import asyncio
import json

import pytest

import phone_app
from app.patient_repository import InMemoryPatientRepository
from app.question_loader import HOOS_JR_QUESTIONS
from app.survey_engine import SafeSurveyEngine
from app.telephony.call_session import lowest_confidence
from app.telephony.deepgram_stt import parse_message
from app.telephony.deepgram_tts import SpeechSynthesisError
from app.telephony.prompt_cache import MAX_ENTRY_BYTES, SpeechCache, fixed_prompts


def _results(transcript: str, confidence: object, *, include: bool = True) -> str:
    alternative: dict[str, object] = {"transcript": transcript}
    if include:
        alternative["confidence"] = confidence
    return json.dumps(
        {"type": "Results", "is_final": True, "channel": {"alternatives": [alternative]}}
    )


def test_parse_message_exposes_recognizer_confidence():
    event = parse_message(_results("mild", 0.42))
    assert event.kind == "transcript"
    assert event.text == "mild"
    assert event.is_final
    assert event.confidence == pytest.approx(0.42)


@pytest.mark.parametrize("confidence", ["0.9", None, True, {"x": 1}])
def test_parse_message_drops_invalid_confidence(confidence):
    assert parse_message(_results("mild", confidence)).confidence is None


def test_parse_message_without_confidence_field_keeps_legacy_shape():
    event = parse_message(_results("mild", None, include=False))
    assert event.text == "mild"
    assert event.confidence is None


def test_lowest_confidence_keeps_the_weakest_fragment():
    assert lowest_confidence(None, None) is None
    assert lowest_confidence(None, 0.9) == 0.9
    assert lowest_confidence(0.9, None) == 0.9
    assert lowest_confidence(0.9, 0.3) == 0.3
    assert lowest_confidence(0.3, 0.9) == 0.3


def _engine() -> SafeSurveyEngine:
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    engine.start()
    return engine


def test_clear_selection_is_accepted_immediately():
    engine = _engine()
    prompt, answer = engine.handle_response("mild", confidence=0.95)
    assert answer is not None and answer.normalized_value == "mild"
    assert answer.acceptance_method == "explicit_selection"
    assert HOOS_JR_QUESTIONS[1].prompt in prompt


def test_unknown_confidence_preserves_legacy_direct_acceptance():
    engine = _engine()
    prompt, answer = engine.handle_response("mild")
    assert answer is not None and answer.normalized_value == "mild"
    assert engine.session.current_index == 1


def test_low_confidence_selection_is_read_back_then_confirmed():
    engine = _engine()
    prompt, answer = engine.handle_response("mild", confidence=0.4)
    assert answer is not None and not answer.confirmed
    assert engine.session.answers == []
    assert engine.session.state == "awaiting_confirmation"
    assert engine.session.current_index == 0
    assert "I think I heard mild" in prompt
    assert "hip pain" in prompt.lower()
    # The read-back does not re-read the whole scale.
    assert "moderate" not in prompt.lower()

    prompt, answer = engine.handle_response("yes", confidence=0.99)
    assert answer is not None and answer.normalized_value == "mild"
    assert answer.confirmed
    assert engine.session.current_index == 1
    assert HOOS_JR_QUESTIONS[1].prompt in prompt


def test_low_confidence_read_back_can_be_corrected():
    engine = _engine()
    engine.handle_response("mild", confidence=0.4)
    prompt, answer = engine.handle_response("no", confidence=0.99)
    assert answer is None
    assert engine.session.current_index == 0
    assert engine.session.pending_answer is None
    assert engine.session.answers == []


def test_low_confidence_never_accepts_a_value_outside_the_allowed_options():
    engine = _engine()
    engine.handle_response("mild", confidence=0.1)
    pending = engine.session.pending_answer
    assert pending is not None
    assert pending.normalized_value in HOOS_JR_QUESTIONS[0].answer_options


def test_low_confidence_gibberish_still_follows_the_clarification_path():
    engine = _engine()
    before = engine.session.clarification_attempts
    prompt, answer = engine.handle_response("blorp", confidence=0.2)
    assert answer is None
    assert engine.session.clarification_attempts == before + 1
    assert engine.session.pending_answer is None


def test_confidence_threshold_is_configurable():
    engine = SafeSurveyEngine(
        InMemoryPatientRepository(), "RGN-0417", confirm_below_confidence=0.0
    )
    engine.start()
    _, answer = engine.handle_response("mild", confidence=0.05)
    assert answer is not None and answer.normalized_value == "mild"


# --- prompt cache ---------------------------------------------------------


def test_cache_hit_and_miss_are_keyed_by_model_and_text():
    cache = SpeechCache()
    assert cache.get("aura-2-thalia-en", "Hello") is None
    cache.put("aura-2-thalia-en", "Hello", b"\xff" * 160)
    assert cache.get("aura-2-thalia-en", "Hello") == b"\xff" * 160
    assert cache.get("aura-2-luna-en", "Hello") is None
    assert cache.get("aura-2-thalia-en", "Hello ") == b"\xff" * 160
    assert cache.hits == 2 and cache.misses == 2


def test_cache_is_bounded_and_rejects_empty_or_huge_audio():
    cache = SpeechCache(max_entries=2)
    cache.put("m", "a", b"1")
    cache.put("m", "b", b"2")
    cache.put("m", "c", b"3")
    assert cache.get("m", "a") is None
    assert cache.get("m", "c") == b"3"
    cache.put("m", "empty", b"")
    assert cache.get("m", "empty") is None
    cache.put("m", "huge", b"x" * (MAX_ENTRY_BYTES + 1))
    assert cache.get("m", "huge") is None
    assert len(cache) == 2


def test_warm_fills_the_cache_and_swallows_individual_failures():
    cache = SpeechCache()
    calls: list[str] = []

    async def synth(text: str, key: str, model: str) -> bytes:
        calls.append(text)
        if text == "bad":
            raise SpeechSynthesisError("boom")
        if text == "worse":
            raise RuntimeError("unexpected")
        return text.encode()

    added = asyncio.run(
        cache.warm(["good", "bad", "worse", "good"], "key", "m", lambda t: [t], synth)
    )
    assert added == 1
    assert sorted(calls) == ["bad", "good", "worse"]
    assert cache.get("m", "good") == b"good"
    assert cache.get("m", "bad") is None


def test_fixed_prompts_cover_the_verbatim_question_bank_but_not_llm_instructions():
    prompts = fixed_prompts()
    joined = "\n".join(prompts)
    assert HOOS_JR_QUESTIONS[0].prompt in joined
    assert all(p.strip() == p and p for p in prompts)
    assert len(prompts) == len(set(prompts)), [p for p in prompts if prompts.count(p) > 1]
    assert not any("You interpret replies" in p for p in prompts)


def test_bridge_synthesize_uses_cache_then_falls_back_to_live(monkeypatch):
    settings = load_settings_with_key(monkeypatch)
    cache = SpeechCache()
    calls: list[str] = []

    async def fake_synth(text: str, key: str, model: str) -> bytes:
        calls.append(text)
        return b"live-" + text.encode()

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", fake_synth)
    bridge = phone_app.MediaStreamBridge(
        websocket=None, settings=settings, repository=InMemoryPatientRepository(),
        persistence=None, tickets=None, speech_cache=cache,
    )
    cache.put(settings.tts_model, "Hi there", b"cached")
    assert asyncio.run(bridge._synthesize("Hi there")) == b"cached"
    assert calls == []
    assert asyncio.run(bridge._synthesize("New text")) == b"live-New text"
    assert asyncio.run(bridge._synthesize("New text")) == b"live-New text"
    assert calls == ["New text"]


def test_bridge_without_cache_synthesizes_live_every_time(monkeypatch):
    settings = load_settings_with_key(monkeypatch)
    calls: list[str] = []

    async def fake_synth(text: str, key: str, model: str) -> bytes:
        calls.append(text)
        return b"x"

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", fake_synth)
    bridge = phone_app.MediaStreamBridge(
        websocket=None, settings=settings, repository=InMemoryPatientRepository(),
        persistence=None, tickets=None,
    )
    asyncio.run(bridge._synthesize("a"))
    asyncio.run(bridge._synthesize("a"))
    assert calls == ["a", "a"]


def test_prompt_cache_env_switch():
    assert phone_app.prompt_cache_enabled({})
    assert phone_app.prompt_cache_enabled({"PROMPT_CACHE": "1"})
    assert not phone_app.prompt_cache_enabled({"PROMPT_CACHE": "0"})
    assert not phone_app.prompt_cache_enabled({"PROMPT_CACHE": "false"})


def test_warm_prompt_cache_failure_does_not_raise(monkeypatch):
    settings = load_settings_with_key(monkeypatch)

    async def exploding(*args, **kwargs):
        raise RuntimeError("network down")

    async def run() -> int | None:
        monkeypatch.setattr(SpeechCache, "warm", exploding)
        task = phone_app.warm_prompt_cache(SpeechCache(), settings)
        assert task is not None
        return await task

    assert asyncio.run(run()) == 0


def test_warm_prompt_cache_skips_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    from app.telephony.config import load_settings

    async def run():
        return phone_app.warm_prompt_cache(SpeechCache(), load_settings())

    assert asyncio.run(run()) is None


def load_settings_with_key(monkeypatch):
    from app.telephony.config import load_settings

    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("PROMPT_CACHE", "1")
    return load_settings()
