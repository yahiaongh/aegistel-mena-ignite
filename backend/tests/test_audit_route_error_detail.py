import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_module


def test_audit_request_defaults_transaction_type():
    # API ergonomics regression: the demo curl omits `transaction_type`; it
    # must default to WIRE_TRANSFER instead of failing with a 422.
    from app.schemas.telemetry import AuditRequest

    req = AuditRequest(
        msisdn="+99999991001",
        amount=1500.0,
        current_location={"latitude": 25.2, "longitude": 55.2},
    )
    assert req.transaction_type == "WIRE_TRANSFER"
    assert req.request_qod_slice is False


def test_audit_route_accepts_minimal_payload(monkeypatch):
    async def fake_audit(request, progress_callback=None):
        from app.agents.graph_orchestrator import execute_audit

        return await execute_audit(request, progress_callback)

    monkeypatch.setattr(main_module, "execute_audit", fake_audit)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/audit",
            json={
                "msisdn": "+99999991001",
                "amount": 1500.0,
                "current_location": {"latitude": 25.2, "longitude": 55.2},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"APPROVED", "REJECTED", "BLOCKED", "STEP_UP_REQUIRED", "MANUAL_REVIEW"}


def test_audit_route_surfaces_detail_for_unhandled_errors(monkeypatch):
    async def boom(request):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(main_module, "execute_audit", boom)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/audit",
            json={
                "msisdn": "+99999991000",
                "amount": 50000.0,
                "transaction_type": "WIRE_TRANSFER",
                "current_location": {"latitude": 24.7, "longitude": 46.7},
                "request_qod_slice": True,
            },
        )

    assert response.status_code == 429
    payload = response.json()
    assert payload["error"] == "audit_pipeline_failure"
    assert payload["detail"] == "quota exhausted"
    assert payload["type"] == "RuntimeError"
