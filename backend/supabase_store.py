"""Server-only, atomic persistence for the integrated patient records.

Uses a separate table from the optional standalone voice-demo schema. No
client receives the service key or raw link tokens. Private phone destinations
are exposed only through the dedicated authenticated clinician database view.
"""

from copy import deepcopy
import os
from urllib.parse import urlsplit

import httpx


class SupabaseStoreError(OSError):
    pass


class SupabasePatientStore:
    def __init__(self, url: str, key: str, transport=None):
        parsed = urlsplit(url)
        if not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise SupabaseStoreError("Invalid Supabase URL configuration")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise SupabaseStoreError("Supabase requires HTTPS")
        if not key:
            raise SupabaseStoreError("Supabase service key is not configured")
        self.url = url.rstrip("/") + "/rest/v1"
        self.key = key
        self.transport = transport
        self.saved: dict[str, dict] = {}
        self.revisions: dict[str, int] = {}

    @classmethod
    def from_env(cls):
        return cls(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

    def _request(self, method, path, **kwargs):
        try:
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False,
                              transport=self.transport) as client:
                response = client.request(method, self.url + path, headers={
                    "apikey": self.key, "Authorization": "Bearer " + self.key,
                    "Content-Type": "application/json",
                }, **kwargs)
            if response.status_code not in {200, 201}:
                # Never include provider bodies, credentials or patient records.
                raise SupabaseStoreError("Supabase patient store rejected the operation; check schema and concurrent updates")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupabaseStoreError("Supabase patient store is unavailable") from exc

    def load(self) -> dict[str, dict]:
        records, revisions = {}, {}
        offset = 0
        while True:
            rows = self._request("GET", "/walking_patient_records", params={
                "select": "patient_id,record,revision", "order": "patient_id.asc",
                "limit": "200", "offset": str(offset),
            })
            if not isinstance(rows, list):
                raise SupabaseStoreError("Invalid patient store response")
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    raise SupabaseStoreError("Invalid patient record")
                pid, record, revision = row.get("patient_id"), row.get("record"), row.get("revision")
                if (not isinstance(pid, str) or not isinstance(record, dict)
                    or record.get("patient_id") != pid or type(revision) is not int or revision < 1
                    or pid in records):
                    raise SupabaseStoreError("Invalid patient identity or revision")
                records[pid], revisions[pid] = record, revision
            offset += len(rows)
        self.saved, self.revisions = deepcopy(records), revisions
        return records

    def save(self, records: dict[str, dict]) -> None:
        if not set(self.saved).issubset(records):
            raise SupabaseStoreError("Patient deletion is not supported")
        changed = {pid: record for pid, record in records.items() if self.saved.get(pid) != record}
        if not changed:
            return
        changes = [{"patient_id": pid, "record": record,
                    "expected_revision": self.revisions.get(pid, 0)} for pid, record in sorted(changed.items())]
        result = self._request("POST", "/rpc/commit_walking_patient_records", json={"changes": changes})
        expected = {pid: self.revisions.get(pid, 0) + 1 for pid in changed}
        if (not isinstance(result, list) or len(result) != len(expected)
            or any(not isinstance(row, dict) for row in result)
            or {row.get("patient_id"): row.get("revision") for row in result} != expected):
            raise SupabaseStoreError("Patient store write outcome could not be verified")
        self.saved.update(deepcopy(changed))
        self.revisions.update(expected)
