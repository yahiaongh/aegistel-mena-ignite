# backend/tests/test_qdrant_persistence.py
"""Qdrant persistence: audit history + feedback must survive a restart.

The product promise: if the local disk is wiped or the server restarts, the
durable Qdrant mirror still returns the full audit history and every feedback
record. These tests emulate a persistent Qdrant cluster (in-memory fake
implementing the exact qdrant-client surface the store uses), write through one
engine instance, then build a FRESH engine (empty local file, simulating a
restart on fresh storage) and assert the records come back from the mirror.

No network is touched: the qdrant-client factory is monkeypatched to the fake.
"""
import types

import pytest
from fastapi.testclient import TestClient

from app.agents import memory_agent
from app.core.config import settings
from app.feedback_store import list_feedback, submit_feedback
from app.main import app
from app.qdrant_store import QdrantStore, audit_point_id

AUDIT_COLLECTION = "aegistel_audit_history"
FEEDBACK_COLLECTION = "aegistel_feedback"


class FakeQdrantClient:
    """In-memory Qdrant cluster: {collection: {str pid: payload}}."""

    def __init__(self, **kwargs):
        self.collections = {}
        self.indexes = {}
        self.kwargs = kwargs

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, vectors_config=None, on_disk_payload=None):
        self.collections.setdefault(collection_name, {})

    def create_payload_index(self, collection_name, field_name, field_schema, **kwargs):
        self.indexes.setdefault(collection_name, set()).add(field_name)

    def upsert(self, collection_name, points, wait=True):
        store = self.collections.setdefault(collection_name, {})
        for p in points:
            store[str(p.id)] = dict(p.payload)
        return types.SimpleNamespace(status="completed")

    def scroll(self, collection_name, scroll_filter=None, limit=10, offset=None,
               with_payload=True, with_vectors=False, **kwargs):
        store = self.collections.get(collection_name, {})
        items = [(k, p) for k, p in store.items() if self._matches(p, scroll_filter)]
        if offset is not None:
            items = items[int(offset):]
        page = items[:limit]
        records = [types.SimpleNamespace(payload=p) for _, p in page]
        next_offset = int(offset or 0) + len(page) if len(items) > len(page) else None
        return records, next_offset

    def delete(self, collection_name, points_selector=None, wait=True, **kwargs):
        store = self.collections.get(collection_name, {})
        filt = getattr(points_selector, "filter", None)
        self.collections[collection_name] = {
            k: p for k, p in store.items() if not self._matches(p, filt)
        }
        return types.SimpleNamespace(status="completed")

    def count(self, collection_name, count_filter=None, exact=True):
        store = self.collections.get(collection_name, {})
        return types.SimpleNamespace(count=sum(1 for p in store.values() if self._matches(p, count_filter)))

    @staticmethod
    def _matches(payload, filter_obj):
        if filter_obj is None:
            return True
        for cond in getattr(filter_obj, "must", []) or []:
            expected = cond.match.value if cond.match is not None else None
            if str(payload.get(cond.key)) != str(expected):
                return False
        return True


def _attached(store, cluster):
    store._client = cluster
    store._force = True
    return store


def _engine(cluster, local_file):
    e = memory_agent.NetworkMemoryEngine()
    e.qdrant = _attached(e.qdrant, cluster)
    e._local_store = e._load_local_store()
    return e


@pytest.fixture
def cluster(monkeypatch):
    fake = FakeQdrantClient()
    monkeypatch.setattr("app.qdrant_store.QdrantClient", lambda **kw: fake)
    return fake


@pytest.fixture
def isolated_local(monkeypatch, tmp_path):
    p = tmp_path / "local.jsonl"
    monkeypatch.setattr(memory_agent, "LOCAL_STORE_PATH", p)
    return p


def test_audit_history_restored_from_qdrant_after_fresh_boot(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident(
        "+99999991040", "WIRE_TRANSFER status=APPROVED",
        {"status": "APPROVED", "risk_score": "LOW", "amount": 1000},
    )
    assert cluster.count(AUDIT_COLLECTION).count == 1

    restart = _engine(cluster, isolated_local)
    incidents = restart.list_all_incidents("+99999991040")
    assert len(incidents) == 1
    assert incidents[0]["metadata"]["status"] == "APPROVED"
    assert incidents[0]["user_id"] == "99999991040"


def test_client_init_creates_filter_field_indexes(cluster):
    """Qdrant cloud 400s filtered scrolls without a keyword index on the filter
    field; the store must provision indexes for user_id/tenant_id/created_at."""
    store = QdrantStore(AUDIT_COLLECTION, enabled=True)
    assert store.upsert(
        audit_point_id({"user_id": "99999991041", "text": "index-proof",
                        "metadata": {}, "created_at": "2026-09-12T00:00:00Z"}),
        {"user_id": "99999991041", "text": "index-proof", "metadata": {},
         "created_at": "2026-09-12T00:00:00Z"},
    )
    assert cluster.indexes.get(AUDIT_COLLECTION) == {"user_id", "tenant_id", "created_at"}


def test_dual_write_persists_to_local_and_qdrant(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident("+99999991042", "text-a", {"status": "APPROVED", "amount": 42})
    with isolated_local.open() as fh:
        assert len([ln for ln in fh if ln.strip()]) == 1
    assert cluster.count(AUDIT_COLLECTION, {"user_id": "99999991042"}).count == 1


def test_list_merges_local_and_qdrant_without_duplicates(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident("+99999991043", "both-stores", {"status": "BLOCKED", "amount": 900})
    assert len(engine.list_all_incidents("+99999991043")) == 1

    cluster.upsert(AUDIT_COLLECTION, [
        types.SimpleNamespace(
            id=audit_point_id({
                "user_id": "99999991043", "text": "remote-only",
                "metadata": {"status": "REJECTED"}, "created_at": "2026-01-02T00:00:00Z",
            }),
            payload={
                "user_id": "99999991043", "text": "remote-only",
                "metadata": {"status": "REJECTED"}, "created_at": "2026-01-02T00:00:00Z",
            },
        )
    ])
    assert len(engine.list_all_incidents("+99999991043")) == 2


def test_tenant_isolation_survives_restart(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident("+99999991044", "bank-b", {"status": "APPROVED", "tenant_id": "bank-b"})
    engine.record_incident("+99999991044", "demo", {"status": "REJECTED"})

    restart = _engine(cluster, isolated_local)
    assert [i["metadata"]["status"] for i in restart.list_all_incidents("+99999991044", tenant_id="bank-b")] == ["APPROVED"]
    assert [i["metadata"]["status"] for i in restart.list_all_incidents("+99999991044", tenant_id="demo")] == ["REJECTED"]


def test_find_audit_decision_works_from_qdrant_after_restart(cluster, isolated_local):
    _engine(cluster, isolated_local).record_incident(
        "+99999991045", "decision",
        {"status": "STEP_UP_REQUIRED", "audit_id": "aud-77", "qod_recommended": True},
    )
    decision = _engine(cluster, isolated_local).find_audit_decision("aud-77", "+99999991045", "demo")
    assert decision is not None
    assert decision["metadata"]["status"] == "STEP_UP_REQUIRED"


def test_clear_all_wipes_qdrant_mirror(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident("+99999991046", "wipe-me", {"status": "APPROVED"})
    assert cluster.count(AUDIT_COLLECTION).count == 1
    engine.clear_all_memory()
    assert cluster.count(AUDIT_COLLECTION).count == 0


def test_tenant_clear_removes_only_that_tenant(cluster, isolated_local):
    engine = _engine(cluster, isolated_local)
    engine.record_incident("+99999991047", "b", {"status": "APPROVED", "tenant_id": "bank-b"})
    engine.record_incident("+99999991047", "d", {"status": "REJECTED"})
    engine.clear_tenant_memory("bank-b")
    assert engine.list_all_incidents("+99999991047", tenant_id="bank-b") == []
    assert len(engine.list_all_incidents("+99999991047", tenant_id="demo")) == 1


def test_history_endpoint_returns_qdrant_restored_records(cluster, monkeypatch, tmp_path):
    if not settings.AEGISTEL_ADMIN_KEY:
        settings.AEGISTEL_ADMIN_KEY = "test-operator-key-aegistel"
    admin = settings.AEGISTEL_ADMIN_KEY
    monkeypatch.setattr(memory_agent, "LOCAL_STORE_PATH", tmp_path / "empty.jsonl")

    _engine(cluster, tmp_path / "empty.jsonl").record_incident(
        "+99999991048", "WIRE_TRANSFER status=APPROVED",
        {"status": "APPROVED", "risk_score": "LOW", "amount": 1000},
    )

    me = memory_agent.memory_engine
    me.qdrant = _attached(me.qdrant, cluster)
    me._local_store = []

    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/history/+99999991048",
            headers={"Authorization": f"Bearer {admin}"},
        )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert resp.json()["incidents"][0]["status"] == "APPROVED"


def test_feedback_restored_from_qdrant_after_fresh_boot(cluster, monkeypatch, tmp_path):
    import app.feedback_store as fb
    fb._QDRANT = _attached(fb.QdrantStore("aegistel_feedback"), cluster)
    monkeypatch.setattr(fb, "FEEDBACK_STORE_PATH", tmp_path / "fb.jsonl")

    submit_feedback({"ratings": {"ui": 5}, "comment": "persistent feedback"}, meta={"client_ip": "10.0.0.1"})
    submit_feedback({"ratings": {"verdict": 4}, "mood": "😍"}, meta={})
    assert cluster.count(FEEDBACK_COLLECTION).count == 2

    # Restart on fresh storage: empty local feedback file. Qdrant must restore.
    monkeypatch.setattr(fb, "FEEDBACK_STORE_PATH", tmp_path / "other_empty.jsonl")
    records = list_feedback(limit=50)
    assert len(records) == 2
    assert {r["ratings"].get("ui") or r["ratings"].get("verdict") for r in records} == {5, 4}


def test_feedback_merge_dedupes_local_and_qdrant(cluster, monkeypatch, tmp_path):
    import app.feedback_store as fb
    fb._QDRANT = _attached(fb.QdrantStore("aegistel_feedback"), cluster)
    monkeypatch.setattr(fb, "FEEDBACK_STORE_PATH", tmp_path / "fb.jsonl")

    submitted = submit_feedback({"ratings": {"ui": 5}, "comment": "dup"}, meta={})
    records = list_feedback(limit=50)
    assert len(records) == 1  # same id in both stores -> single record
    assert records[0]["id"] == submitted["id"]
