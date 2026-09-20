import asyncio
from unittest.mock import AsyncMock, patch

from app.main_backend import BackendError
from test_main_integration import answer_survey

pytest_plugins = ["test_main_integration"]


def test_unpublished_call_reservation_never_dials_or_stays_dispatching(harness):
    async def scenario():
        with patch.object(harness.backend, "status", AsyncMock(side_effect=BackendError())):
            snapshot = await harness.service.start(harness.payload)
        assert harness.provider.calls == 0
        assert snapshot.call_status == "failed"
        assert snapshot.error_code == "provider_unavailable"
        assert (await harness.service.start(harness.payload)).call_status == "failed"
        await harness.service.read(harness.payload.call_id)
        assert harness.snapshots[-1]["call_status"] == "failed"
        assert harness.provider.calls == 0
    asyncio.run(scenario())


def test_guidance_describes_auto_capture_and_explicit_live_save(harness):
    async def scenario():
        harness.walks = [
            harness.view("ready", 3, "calibration_completed"),
            harness.view("capturing", 4, "capture_started"),
            harness.view("captured", 5, "capture_completed"),
            harness.view("saved", 6, session_id="persisted-session"),
        ]
        session = await harness.session()
        await answer_survey(session)
        await session._background
        assert any("live capture starts automatically" in text for text in harness.spoken)
        assert any("choose Save this walk" in text for text in harness.spoken)
        assert not any("start capture on the page" in text for text in harness.spoken)
        assert "walking test is saved" in harness.spoken[-1]
    asyncio.run(scenario())


def test_unpublished_sms_reservation_is_definite_failure_without_provider_dispatch(harness):
    async def scenario():
        session = await harness.session()
        original = harness.backend.status

        async def unavailable(snapshot):
            if snapshot.sms_status == "sending":
                raise BackendError()
            await original(snapshot)

        with patch.object(harness.backend, "status", unavailable):
            await answer_survey(session)
            await session._background
        snapshot = await harness.service.read(harness.payload.call_id)
        assert snapshot.survey_status == "stored"
        assert snapshot.sms_status == "failed"
        assert snapshot.error_code == "provider_unavailable"
        assert harness.provider.messages == 0
        await harness.service.retry_submission(harness.payload.call_id)
        assert harness.provider.messages == 0
    asyncio.run(scenario())
