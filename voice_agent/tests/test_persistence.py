from app.persistence import InMemoryPersistence


def test_persistence_tracks_transcript_and_answers():
    repo = InMemoryPersistence()
    record = repo.start_call("call-123", "RGN-0417", "orthopedic")
    repo.append_transcript("call-123", "mild")
    repo.record_answer("call-123", {"question_id": "hoos_stairs", "value": "mild", "confirmed": True})
    repo.complete_call("call-123")

    assert record.status == "completed"
    assert record.transcript == ["mild"]
    assert record.answers[0]["question_id"] == "hoos_stairs"
    assert record.final_status == "completed"
