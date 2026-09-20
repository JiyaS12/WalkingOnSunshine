import pytest


@pytest.fixture(autouse=True)
def configured_patient_links(monkeypatch):
    """Use explicit, deterministic patient-link configuration in API tests."""

    monkeypatch.setenv(
        "PATIENT_LINK_SIGNING_SECRET",
        "test-only-patient-link-secret-32-bytes-minimum",
    )
    monkeypatch.setenv("PATIENT_APP_BASE_URL", "https://patient.example.test")
    monkeypatch.setenv("PATIENT_LINK_TTL_SECONDS", "900")
