"""Allowlisted, read-only clinician projection of integrated patient storage."""

from copy import deepcopy
from datetime import datetime, timezone

import store


def pick(value: dict, fields: str) -> dict:
    return {key: deepcopy(value[key]) for key in fields.split() if key in value}


def project_record(record: dict) -> dict:
    result = pick(record, "patient_id name age cohort condition_category condition_source active_call_id")
    calls = []
    for call in record.get("calls", []):
        item = pick(call, "call_id patient_id attempt_id condition_category call_status survey_status survey_id sms_status sms_attempt error_code created_at updated_at needs_human_review")
        # Deliberately available only in this clinician-authenticated projection.
        item["destination_phone"] = call.get("_destination_phone")
        if any(survey.get("call_id") == call["call_id"] and any((survey.get("condition_survey") or {}).get(key) for key in ("needs_human_review", "skipped", "unanswered_questions")) for survey in record.get("surveys", [])):
            item["needs_human_review"] = True
        walking = call.get("walking") or {}
        item["walking"] = pick(walking, "status last_sequence last_event session_id")
        item["walking"]["events"] = [
            pick(event, "event_id call_id attempt_id sequence event error_code received_at")
            for event in walking.get("events", [])
        ]
        calls.append(item)
    result["calls"] = calls
    result["surveys"] = []
    for survey in record.get("surveys", []):
        item = pick(survey, "patient_id patient_name submission_kind survey_id pain_scale dizziness dizziness_notes primary_complaints call_id recorded_at")
        history = survey.get("fall_history")
        item["fall_history"] = pick(history, "falls_last_6_months injured last_fall_description") if history else None
        condition = survey.get("condition_survey")
        item["condition_survey"] = None
        if condition:
            item["condition_survey"] = pick(condition, "instrument version condition_category needs_human_review skipped")
            item["condition_survey"]["needs_human_review"] = bool(condition.get("needs_human_review") or condition.get("unanswered_questions") or condition.get("skipped"))
            item["condition_survey"]["unanswered_questions"] = [
                pick(question, "question_id reason clarification_attempts")
                for question in condition.get("unanswered_questions", [])
            ]
            item["condition_survey"]["transcript"] = [
                pick(turn, "speaker text question_id recorded_at")
                for turn in condition.get("transcript", [])
            ]
            item["condition_survey"]["answers"] = [
                pick(answer, "question_id normalized_value confirmed acceptance_method confirmed_at confidence clarification_attempts")
                for answer in condition.get("answers", [])
            ]
        result["surveys"].append(item)
    result["gait_sessions"] = []
    for gait in record.get("gait_sessions", []):
        item = pick(gait, "session_id call_id attempt_id label recorded_at source")
        item["metrics"] = pick(gait.get("metrics") or {}, "stride_length_m asymmetry_pct velocity_degradation_pct fall_risk_score cadence_steps_per_min frame_count leg_length_m stride_ratio knee_flexion_rom_deg peak_ankle_speed_mps gait_detected dropped_frame_pct")
        item["stored_frame_count"] = len(gait.get("frames") or [])
        result["gait_sessions"].append(item)
    return result


def snapshot(query: str = "", offset: int = 0, limit: int = 25) -> dict:
    source, records = store.database_records()
    rows = [project_record(record) for record in records.values()]
    totals = {
        "patients": len(rows),
        "calls": sum(len(row["calls"]) for row in rows),
        "surveys": sum(len(row["surveys"]) for row in rows),
        "walking": sum(len(row["gait_sessions"]) for row in rows),
    }
    wanted = query.strip().casefold()
    if wanted:
        rows = [row for row in rows if any(wanted in str(value or "").casefold() for value in [
            row["patient_id"], row.get("name"),
            *(call.get("destination_phone") for call in row["calls"]),
            *(call.get("call_id") for call in row["calls"]),
        ])]
    rows.sort(key=lambda row: row["patient_id"])
    return {
        "source": source, "read_only": True,
        "read_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals, "total": len(rows), "offset": offset, "limit": limit,
        "patients": rows[offset:offset + limit],
    }
