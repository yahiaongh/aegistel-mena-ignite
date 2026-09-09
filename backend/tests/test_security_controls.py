import asyncio
import sys
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _rate_limit  # noqa: E402


class _FakeClient:
    host = "203.0.113.7"


class _FakeRequest:
    @property
    def client(self):  # noqa: N802
        return _FakeClient()


class _FakeHttpRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


@pytest.fixture(autouse=True)
def _clean_rate_windows():
    yield
    from app import main as main_module

    main_module._RATE_WINDOWS.clear()


def test_rate_limit_blocks_after_limit_and_includes_retry_after():
    dep = _rate_limit("unit-test-bucket", limit=3, window_seconds=60)
    req = _FakeRequest()
    for _ in range(3):
        dep(req)
    with pytest.raises(HTTPException) as excinfo:
        dep(req)
    assert excinfo.value.status_code == 429
    assert excinfo.value.headers.get("Retry-After") is not None


def test_rate_limit_buckets_are_per_key():
    dep = _rate_limit("unit-test-bucket", limit=3, window_seconds=60)
    other = _rate_limit("unit-test-other", limit=3, window_seconds=60)
    req = _FakeRequest()
    for _ in range(2):
        dep(req)
    other(req)  # different bucket is unaffected


def test_memory_history_respects_tenant_scope(monkeypatch):
    from app.agents.memory_agent import NetworkMemoryEngine

    engine = NetworkMemoryEngine()
    monkeypatch.setattr(engine, "_append_local_store", lambda record: None)
    engine._local_store = []
    try:
        engine.record_incident(
            "+99999991001",
            "clean p2p",
            metadata={"status": "APPROVED", "tenant_id": "bank-a"},
        )
        engine.record_incident(
            "+99999991001",
            "wire risk",
            metadata={"status": "BLOCKED", "tenant_id": "bank-b"},
        )
        engine.record_incident("+99999991001", "legacy record", metadata={"status": "APPROVED"})

        bank_a = engine.list_all_incidents("+99999991001", tenant_id="bank-a")
        bank_b = engine.list_all_incidents("+99999991001", tenant_id="bank-b")
        demo = engine.list_all_incidents("+99999991001", tenant_id="demo")

        assert len(bank_a) == 1 and bank_a[0]["metadata"]["tenant_id"] == "bank-a"
        assert len(bank_b) == 1 and bank_b[0]["metadata"]["tenant_id"] == "bank-b"
        # Records written before tenant tagging default to the "demo" tenant.
        assert len(demo) == 1 and demo[0]["metadata"].get("tenant_id") in (None, "demo")
    finally:
        engine._local_store = []


def test_audit_request_rejects_tenant_id_in_body():
    from app.schemas.telemetry import AuditRequest

    # The public schema has no tenant field and forbids extras: a client that
    # tries to poison another tenant's namespace via JSON is rejected loudly.
    with pytest.raises(ValidationError):
        AuditRequest(
            msisdn="+966500000001",
            amount=10,
            current_location={"latitude": 24.7, "longitude": 46.6},
            tenant_id="bank-a",
        )


def _assert_resolve_tenant(monkeypatch, settings, main_module, **overrides):
    monkeypatch.setattr(settings, "AEGISTEL_TENANT_API_KEYS", "bank-a=ka,bank-b=kb")
    monkeypatch.setattr(main_module, "_TENANT_KEY_MAP_CACHE", None)
    for name, value in overrides.items():
        monkeypatch.setattr(settings, name, value)


def test_resolve_tenant_derives_tenant_from_credential(monkeypatch):
    from app import main as main_module
    from app.core.config import settings
    from app.main import _resolve_tenant

    _assert_resolve_tenant(monkeypatch, settings, main_module, AEGISTEL_ALLOW_ANON_AUDIT=True)

    # Valid tenant key -> that tenant's namespace.
    assert _resolve_tenant(_FakeHttpRequest({"authorization": "Bearer kb"}, {})) == "bank-b"
    assert _resolve_tenant(_FakeHttpRequest({"authorization": "Bearer ka"}, {})) == "bank-a"

    # Anonymous caller -> default namespace only (never a real tenant).
    assert _resolve_tenant(_FakeHttpRequest({}, {})) == settings.AEGISTEL_DEFAULT_TENANT

    # Unknown / forged key -> fails closed.
    with pytest.raises(HTTPException) as excinfo:
        _resolve_tenant(_FakeHttpRequest({"authorization": "Bearer nope"}, {}))
    assert excinfo.value.status_code == 401


def test_resolve_tenant_fails_closed_when_anon_disabled(monkeypatch):
    from app import main as main_module
    from app.core.config import settings
    from app.main import _resolve_tenant, _tenant_key_map

    _assert_resolve_tenant(monkeypatch, settings, main_module, AEGISTEL_ALLOW_ANON_AUDIT=False)
    assert _tenant_key_map() == {"bank-a": "ka", "bank-b": "kb"}

    with pytest.raises(HTTPException) as excinfo:
        _resolve_tenant(_FakeHttpRequest({}, {}))
    assert excinfo.value.status_code == 401


def test_execute_audit_records_and_echoes_server_derived_tenant(monkeypatch):
    from app.agents.graph_orchestrator import execute_audit
    from app.agents.memory_agent import memory_engine
    from app.schemas.telemetry import AuditRequest

    monkeypatch.setattr(memory_engine, "_append_local_store", lambda record: None)
    memory_engine._local_store = []
    try:
        req = AuditRequest(
            msisdn="+966500100001",
            amount=100,
            transaction_type="P2P_TRANSFER",
            current_location={"latitude": 24.7, "longitude": 46.6},
            metadata={"_force_deterministic": True},
        )
        resp = asyncio.run(execute_audit(req, tenant_id="  Bank-B "))
        assert resp.tenant_id == "bank-b"

        recs = memory_engine.list_all_incidents(req.msisdn, tenant_id="bank-b")
        assert recs, "audit should record the incident under the server-derived tenant"
        assert all(r["metadata"]["tenant_id"] == "bank-b" for r in recs)
        # The same audit must NOT leak into the default namespace.
        assert memory_engine.list_all_incidents(req.msisdn, tenant_id="demo") == []
    finally:
        memory_engine._local_store = []


def _configure_tenant_keys(monkeypatch):
    from app import main as main_module
    from app.core.config import settings

    monkeypatch.setattr(settings, "AEGISTEL_TENANT_API_KEYS", "bank-a=ka,bank-b=kb")
    monkeypatch.setattr(main_module, "_TENANT_KEY_MAP_CACHE", None)


def test_audit_decision_never_provisions_qod():
    from fastapi.testclient import TestClient

    from app import main as main_module

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/audit",
            json={
                "msisdn": "+99999991000",
                "amount": 120000,
                "transaction_type": "WIRE_TRANSFER",
                "current_location": {"latitude": 24.0, "longitude": 46.0},
                "request_qod_slice": True,
                "metadata": {"_force_deterministic": True},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    # The decision recommends a QoD step-up but never provisions the session.
    assert payload["qod_recommended"] is True
    assert payload["telemetry"]["qod_session_active"] is False
    assert not any(
        tr.get("name") == "create_qod_session" for tr in payload["telemetry"]["tool_results"]
    )


def test_qod_provision_requires_authentication(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_module

    _configure_tenant_keys(monkeypatch)
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/audit/qod/provision",
            json={"audit_id": str(uuid4()), "msisdn": "+966500100001"},
        )
    assert response.status_code == 401


def test_qod_provision_blocked_by_bank_policy(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_module

    _configure_tenant_keys(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_QOD_POLICY_ENABLED", False)
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/audit/qod/provision",
            json={"audit_id": str(uuid4()), "msisdn": "+966500100001"},
            headers={"Authorization": "Bearer kb"},
        )
    assert response.status_code == 403
    assert "QoD provisioning is disabled" in response.json()["detail"]


def test_qod_provision_succeeds_only_with_policy_and_credential(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_module

    _configure_tenant_keys(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_QOD_POLICY_ENABLED", True)
    with TestClient(main_module.app) as client:
        audit = client.post(
            "/api/v1/audit",
            json={
                "msisdn": "+99999991000",
                "amount": 120000,
                "transaction_type": "WIRE_TRANSFER",
                "current_location": {"latitude": 24.0, "longitude": 46.0},
                "metadata": {"_force_deterministic": True},
            },
            headers={"Authorization": "Bearer kb"},
        )
        assert audit.status_code == 200
        audit_id = audit.json()["audit_id"]
        response = client.post(
            "/api/v1/audit/qod/provision",
            json={"audit_id": audit_id, "msisdn": "+99999991000", "profile": "QOS_E", "duration_seconds": 3600},
            headers={"Authorization": "Bearer kb"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provisioned"] is True
    assert payload["tenant"] == "bank-b"
    assert payload["session"].get("sessionId"), "provisioning must return a session id"

    trail = main_module.memory_engine.list_all_incidents("+99999991000", tenant_id="bank-b")
    assert any(r["metadata"].get("status") == "QOD_PROVISIONED" for r in trail)
    assert all(r["metadata"]["tenant_id"] == "bank-b" for r in trail)
    with TestClient(main_module.app) as client:
        replay = client.post(
            "/api/v1/audit/qod/provision",
            json={"audit_id": audit_id, "msisdn": "+99999991000"},
            headers={"Authorization": "Bearer kb"},
        )
    assert replay.status_code == 409


def test_qod_provision_rejects_unrecommended_or_foreign_audit(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main as main_module

    _configure_tenant_keys(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_QOD_POLICY_ENABLED", True)
    monkeypatch.setattr(main_module.memory_engine, "_append_local_store", lambda record: None)
    main_module.memory_engine._local_store = []
    try:
        with TestClient(main_module.app) as client:
            clean_audit = client.post(
                "/api/v1/audit",
                json={
                    "msisdn": "+99999991001",
                    "amount": 100,
                    "transaction_type": "P2P_TRANSFER",
                    "current_location": {"latitude": 24.7, "longitude": 46.6},
                    "metadata": {"_force_deterministic": True},
                },
                headers={"Authorization": "Bearer ka"},
            )
            assert clean_audit.status_code == 200
            response = client.post(
                "/api/v1/audit/qod/provision",
                json={"audit_id": clean_audit.json()["audit_id"], "msisdn": "+99999991001"},
                headers={"Authorization": "Bearer kb"},
            )
        assert response.status_code == 403
        with TestClient(main_module.app) as client:
            same_tenant_response = client.post(
                "/api/v1/audit/qod/provision",
                json={"audit_id": clean_audit.json()["audit_id"], "msisdn": "+99999991001"},
                headers={"Authorization": "Bearer ka"},
            )
        assert same_tenant_response.status_code == 403
    finally:
        main_module.memory_engine._local_store = []
