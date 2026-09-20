from unittest.mock import Mock

import httpx
import pytest

from app import deepgram


@pytest.fixture
def post(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("DEEPGRAM_TTS_MODEL", raising=False)
    mock = Mock()
    monkeypatch.setattr(deepgram, "_client", lambda: Mock(post=mock))
    return mock


def response(status=200, **kwargs):
    return httpx.Response(status, request=httpx.Request("POST", "https://api.deepgram.com/v1/speak"), **kwargs)


def test_speech_sends_verbatim_text_and_returns_mp3(post):
    post.return_value = response(content=b"synthetic-mp3", headers={"content-type": "audio/mpeg"})
    text = "Over the past week, how much hip pain have you experienced going up or down stairs?"
    assert deepgram.synthesize_with_deepgram(text) == b"synthetic-mp3"
    args, kwargs = post.call_args
    assert args[0] == "https://api.deepgram.com/v1/speak"
    assert kwargs["json"] == {"text": text}
    assert kwargs["params"] == {"model": "aura-2-thalia-en", "encoding": "mp3"}
    assert kwargs["headers"]["Authorization"] == "Token synthetic-test-key"


def test_custom_voice_is_configurable(post, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_TTS_MODEL", "aura-2-helena-en")
    post.return_value = response(content=b"synthetic-mp3", headers={"content-type": "audio/mpeg"})
    deepgram.synthesize_with_deepgram("Hello.")
    assert post.call_args.kwargs["params"]["model"] == "aura-2-helena-en"


@pytest.mark.parametrize("text", ["", " ", "x" * 2001])
def test_invalid_speech_never_calls_provider(post, text):
    with pytest.raises(ValueError):
        deepgram.synthesize_with_deepgram(text)
    post.assert_not_called()


@pytest.mark.parametrize("code", [401, 403, 402, 429, 500])
def test_provider_errors_are_actionable_without_exposing_credentials(post, code):
    post.return_value = response(code, json={"error": "private provider details"})
    with pytest.raises(RuntimeError) as exc:
        deepgram.synthesize_with_deepgram("Hello.")
    assert "Deepgram" in str(exc.value)
    assert "synthetic-test-key" not in str(exc.value)
    assert "private provider details" not in str(exc.value)


def test_network_error_is_actionable(post):
    post.side_effect = httpx.ConnectError("private connection details")
    with pytest.raises(RuntimeError, match="Could not reach Deepgram"):
        deepgram.synthesize_with_deepgram("Hello.")


def test_non_audio_response_is_rejected(post):
    post.return_value = response(json={"not": "audio"})
    with pytest.raises(RuntimeError, match="no playable speech"):
        deepgram.synthesize_with_deepgram("Hello.")


def test_transcription_preserves_recording_type_and_returns_text(post):
    post.return_value = response(json={"results": {"channels": [{"alternatives": [{"transcript": " Mild. "}]}]}})
    assert deepgram.transcribe_with_deepgram(b"synthetic-mp4", "audio/mp4") == "Mild."
    assert post.call_args.kwargs["content"] == b"synthetic-mp4"
    assert post.call_args.kwargs["headers"]["Content-Type"] == "audio/mp4"


def test_no_speech_explains_how_to_retry(post):
    post.return_value = response(json={"results": {"channels": [{"alternatives": [{"transcript": ""}]}]}})
    with pytest.raises(RuntimeError, match="No speech was detected"):
        deepgram.transcribe_with_deepgram(b"silent-recording", "audio/webm")


def test_missing_key_does_not_send_a_request(post, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(RuntimeError, match="not configured"):
        deepgram.synthesize_with_deepgram("Hello.")
    post.assert_not_called()


def test_stream_yields_first_audio_without_reading_the_remainder_and_closes(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    emitted = []
    closed = []

    class AudioStream(httpx.SyncByteStream):
        def __iter__(self):
            emitted.append("first")
            yield b"first-mp3-frames"
            emitted.append("rest")
            yield b"rest-of-the-audio"

        def close(self):
            closed.append(True)

    def transport(request):
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=AudioStream())

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(deepgram, "_client", lambda: client)
        stream = deepgram.stream_speech_with_deepgram("A long question.")
        assert next(stream) == b"first-mp3-frames"
        assert emitted == ["first"]
        stream.close()  # Simulates the patient interrupting playback.
        assert closed == [True]
        assert emitted == ["first"]


@pytest.mark.parametrize("code,content_type,body", [(401, "application/json", b"private error"),
                                                   (200, "application/json", b"not audio"),
                                                   (200, "audio/mpeg", b"")])
def test_invalid_stream_fails_before_any_audio_is_returned(monkeypatch, code, content_type, body):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        code, headers={"content-type": content_type}, content=body,
    ))) as client:
        monkeypatch.setattr(deepgram, "_client", lambda: client)
        with pytest.raises(RuntimeError):
            next(deepgram.stream_speech_with_deepgram("Hello."))
