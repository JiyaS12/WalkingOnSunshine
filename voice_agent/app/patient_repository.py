from __future__ import annotations

from .models import ConditionCategory, PatientRecord


class PatientNotFoundError(LookupError):
    """Raised when a patient code is not present in the lookup set."""


class InMemoryPatientRepository:
    """Tiny repository used by the safe runtime; replaceable with Supabase later."""

    def __init__(self, patients: dict[str, PatientRecord] | None = None):
        self._patients = patients or {
            "RGN-0417": PatientRecord("pt_orthopedic_demo", "RGN-0417", ConditionCategory.ORTHOPEDIC),
            "RGN-0500": PatientRecord("pt_stroke_demo", "RGN-0500", ConditionCategory.STROKE),
        }

    def lookup_patient(self, patient_code: str) -> PatientRecord:
        record = self._patients.get(patient_code.strip())
        if record is None:
            raise PatientNotFoundError(f"No patient found for code '{patient_code}'.")
        return record
