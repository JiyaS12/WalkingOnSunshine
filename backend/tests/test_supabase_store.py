from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from supabase_store import SupabasePatientStore, SupabaseStoreError
import store


class RestFixture:
    def __init__(self):
        self.rows = {}
        self.requests = []
        self.fail = False

    def handle(self, request):
        self.requests.append(request)
        assert request.headers['Authorization'] == 'Bearer test-service-key'
        assert request.headers['apikey'] == 'test-service-key'
        if self.fail:
            return httpx.Response(503, json={'message': 'private provider detail'})
        if request.method == 'GET':
            offset = int(request.url.params['offset'])
            # Deliberately smaller than requested to exercise server-side caps.
            return httpx.Response(200, json=list(self.rows.values())[offset:offset + 1])
        assert request.url.path == '/rest/v1/rpc/commit_walking_patient_records'
        changes = json.loads(request.content)['changes']
        replacement = deepcopy(self.rows)
        result = []
        for change in changes:
            pid = change['patient_id']
            current = replacement.get(pid, {}).get('revision', 0)
            if current != change['expected_revision']:
                return httpx.Response(409, json={'code': '40001'})
            replacement[pid] = {'patient_id': pid, 'record': change['record'], 'revision': current + 1}
            result.append({'patient_id': pid, 'revision': current + 1})
        self.rows = replacement
        return httpx.Response(200, json=result)


def adapter(rest):
    return SupabasePatientStore('https://project.example', 'test-service-key', httpx.MockTransport(rest.handle))


def patient(pid='demo-1'):
    return {'patient_id': pid, 'name': 'Synthetic patient', 'surveys': [], 'gait_sessions': []}


def test_roundtrip_pagination_and_private_phone_association():
    rest = RestFixture()
    first = adapter(rest)
    assert first.load() == {}
    record = patient()
    record['calls'] = [{'call_id': 'call-1', 'attempt_id': 'attempt-1', '_destination_phone': '+15555550123'}]
    first.save({'demo-1': record, 'demo-2': patient('demo-2')})
    second = adapter(rest)
    assert second.load() == {'demo-1': record, 'demo-2': patient('demo-2')}
    assert second.revisions == {'demo-1': 1, 'demo-2': 1}
    before = len(rest.requests)
    second.save(second.saved)
    assert len(rest.requests) == before


def test_concurrent_write_cannot_overwrite_newer_patient_data():
    rest = RestFixture()
    a, b = adapter(rest), adapter(rest)
    a.load()
    a.save({'demo-1': patient()})
    stale = b.load()
    updated = deepcopy(a.saved)
    updated['demo-1']['gait_sessions'] = [{'session_id': 'saved-walk'}]
    a.save(updated)
    stale['demo-1']['name'] = 'Stale update'
    with pytest.raises(SupabaseStoreError):
        b.save(stale)
    assert adapter(rest).load()['demo-1']['gait_sessions'] == [{'session_id': 'saved-walk'}]


def test_outage_never_becomes_empty_results_or_leaks_provider_body():
    rest = RestFixture()
    rest.fail = True
    with pytest.raises(SupabaseStoreError) as caught:
        adapter(rest).load()
    assert 'private provider detail' not in str(caught.value)


def test_mismatched_patient_identity_fails_closed():
    rest = RestFixture()
    rest.rows['demo-1'] = {'patient_id': 'demo-1', 'record': patient('different'), 'revision': 1}
    with pytest.raises(SupabaseStoreError):
        adapter(rest).load()


@pytest.fixture
def integrated_store(monkeypatch):
    rest = RestFixture()
    monkeypatch.setenv('PATIENT_STORE', 'supabase')
    monkeypatch.delenv('SUPABASE_SEED_DEMO', raising=False)
    monkeypatch.setattr(store, '_test_json_store', False)
    monkeypatch.setattr(store, '_patients', None)
    monkeypatch.setattr(store, '_supabase', None)
    monkeypatch.setattr(SupabasePatientStore, 'from_env', classmethod(lambda cls: adapter(rest)))
    return rest


def test_integrated_store_persists_patient_call_and_phone_before_dispatch(integrated_store):
    rest = integrated_store
    store.upsert_survey({'patient_id': 'demo-1', 'patient_name': 'Synthetic patient'})
    call, created = store.reserve_call('demo-1', 'request-1', 'orthopedic', 'fingerprint', '+15555550123')
    assert created
    persisted = adapter(rest).load()['demo-1']
    assert persisted['calls'][0]['call_id'] == call['call_id']
    assert persisted['calls'][0]['_destination_phone'] == '+15555550123'
    assert '_destination_phone' not in call
    assert '+15555550123' not in json.dumps(store.get_patient('demo-1'))
    existing, created = store.ensure_patient('demo-1', {})
    assert not created
    assert '+15555550123' not in json.dumps(existing)
    # Equivalent restart reloads from Supabase, not the local JSON cache.
    store._patients = None
    store._supabase = None
    assert store.get_call('demo-1', call['call_id'])['attempt_id'] == call['attempt_id']


def test_survey_and_walking_save_reload_with_same_patient_call_and_private_phone(integrated_store):
    store.upsert_survey({'patient_id': 'demo-1', 'patient_name': 'Synthetic patient'})
    call, _ = store.reserve_call('demo-1', 'request-1', 'orthopedic', 'fingerprint', '+15555550123')
    store.upsert_survey({
        'patient_id': 'demo-1', 'call_id': call['call_id'], 'submission_kind': 'integrated',
        'condition_survey': {'condition_category': 'orthopedic'},
    })
    session = {
        'call_id': call['call_id'], 'attempt_id': call['attempt_id'],
        'idempotency_key': 'walk-1', 'metrics': {'gait_detected': True}, 'frames': None,
    }
    with pytest.raises(store.Conflict):
        store.add_session('demo-1', {**session, 'attempt_id': 'different-attempt'}, require_active_correlation=True)
    saved = store.add_session('demo-1', session, require_active_correlation=True)
    store._patients = None
    store._supabase = None
    restored = store.get_patient('demo-1')
    assert restored == saved
    durable = adapter(integrated_store).load()['demo-1']
    assert durable['calls'][0]['_destination_phone'] == '+15555550123'
    assert durable['calls'][0]['walking']['status'] == 'saved'
    assert durable['surveys'][-1]['call_id'] == durable['gait_sessions'][0]['call_id'] == call['call_id']
    assert durable['gait_sessions'][0]['attempt_id'] == call['attempt_id']
    assert '+15555550123' not in json.dumps(restored)


def test_failed_database_write_invalidates_cache_without_local_fallback(integrated_store):
    rest = integrated_store
    store.upsert_survey({'patient_id': 'demo-1', 'patient_name': 'Synthetic patient'})
    rest.fail = True
    with pytest.raises(OSError):
        store.set_condition('demo-1', 'orthopedic')
    assert store._patients is None
    with pytest.raises(store.StoreUnavailable):
        store.get_patient('demo-1')
    rest.fail = False
    assert store.get_patient('demo-1')['condition_category'] is None


def test_missing_schema_does_not_fall_back_to_local_seed_patients(integrated_store):
    integrated_store.fail = True
    with pytest.raises(store.StoreUnavailable):
        store.get_patient('RGN-0417')
    assert store._patients is None


def test_migration_is_additive_and_service_role_only():
    sql = (Path(__file__).resolve().parents[2] / 'supabase/migrations/20260920080000_integrated_patient_records.sql').read_text().lower()
    assert 'drop table' not in sql and 'truncate' not in sql
    assert 'enable row level security' in sql
    assert 'security invoker' in sql
    assert 'from public, anon, authenticated' in sql
    assert 'to service_role' in sql
    assert "p.revision = expected" in sql
    assert "g.value ->> 'attempt_id' = c.value ->> 'attempt_id'" in sql
