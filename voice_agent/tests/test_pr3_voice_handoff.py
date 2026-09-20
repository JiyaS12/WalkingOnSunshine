import asyncio

from app.gait_handoff import GaitHandoffService
from app.voice_adapter import VoiceAdapter


def test_voice_adapter_has_expected_conversation_scripts():
    adapter = VoiceAdapter()
    intro = adapter.doctor_intro("orthopedic")
    closing = adapter.closing_script()

    assert "automated check-in from your doctor" in intro.text.lower()
    assert "survey is complete" in closing.text.lower()
    assert "send" not in closing.text.lower()


def test_handoff_service_prepares_payload_without_sending_link():
    async def fetch(patient_code: str, call_id: str | None) -> str:
        return f"https://walk.example.org/patient/{patient_code}?token=stub"

    service = GaitHandoffService(link_fetcher=fetch)
    payload = asyncio.run(service.prepare("RGN-0417", "orthopedic"))

    assert payload.patient_code == "RGN-0417"
    assert payload.condition_category == "orthopedic"
    assert payload.link is not None
    assert payload.status == "prepared"
