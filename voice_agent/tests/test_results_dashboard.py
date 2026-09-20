from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.operator_auth import OperatorAuth
from app.patient_repository import InMemoryPatientRepository
from app.persistence import CompositePersistence, InMemoryPersistence
from app.question_loader import HOOS_JR_QUESTIONS
from voice_app import create_app

TOKEN = "dashboard-test-operator-0123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def client_for(store):
    return TestClient(create_app(persistence=store, auth=OperatorAuth(TOKEN)))


def test_public_shell_never_exposes_results_without_authentication():
    memory = InMemoryPersistence()
    patient = InMemoryPatientRepository().lookup_patient("RGN-0417")
    memory.persist_session_start("local", patient, "private synthetic transcript")
    with client_for(memory) as client:
        page = client.get("/results")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert "private synthetic transcript" not in page.text
        assert client.get("/api/results").status_code == 401
        assert client.get("/api/results", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/api/results", headers=HEADERS)
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["source"] == "in_memory"
        assert "restart" in data["warning"]
        assert data["results"][0]["started_at"]
        assert data["results"][0]["completed_at"] is None
        for question in HOOS_JR_QUESTIONS:
            assert data["question_catalog"][question.id]["prompt"] == question.prompt
        assert client.get("/static/results.js").status_code == 200
        assert client.get("/static/results.css").status_code == 200


def test_missing_operator_configuration_fails_closed():
    with TestClient(create_app(persistence=InMemoryPersistence(), auth=OperatorAuth(None))) as client:
        assert client.get("/api/results", headers=HEADERS).status_code == 503


def test_healthy_empty_database_reports_supabase():
    database = Mock()
    database.list_results.return_value = []
    with client_for(CompositePersistence(InMemoryPersistence(), database)) as client:
        data = client.get("/api/results?patient_code=RGN-0417", headers=HEADERS).json()
    assert data["results"] == []
    assert data["source"] == "supabase"
    assert data["warning"] is None
    database.list_results.assert_called_once_with("RGN-0417")


def test_partial_database_failure_keeps_recovered_rows_and_labels_them():
    memory = InMemoryPersistence()
    memory.start_call("lost", "RGN-0417", "orthopedic")
    database = Mock()
    database.list_results.return_value = [{"follow_up_label": "live:stored"}]
    data = CompositePersistence(memory, database).results_snapshot()
    assert data["source"] == "mixed"
    assert "temporary" in data["warning"]
    assert data["results"][0]["storage_source"] == "supabase"
    assert data["results"][1]["session_id"] == "lost"
    assert data["results"][1]["storage_source"] == "in_memory"


def test_read_failure_returns_explicit_fallback_without_leaking_exception():
    database = Mock()
    database.list_results.side_effect = RuntimeError("sensitive connection details")
    with client_for(CompositePersistence(InMemoryPersistence(), database)) as client:
        response = client.get("/api/results", headers=HEADERS)
    assert response.json()["source"] == "in_memory"
    assert "Supabase is unavailable" in response.json()["warning"]
    assert "sensitive connection details" not in response.text


def test_completion_time_is_stable():
    memory = InMemoryPersistence()
    memory.start_call("local", "RGN-0417", "orthopedic")
    memory.complete_call("local")
    first = memory.list_results()[0]["completed_at"]
    assert first
    memory.complete_call("local")
    assert memory.list_results()[0]["completed_at"] == first
