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
    # The real audit pipeline already has full end-to-end coverage in
    # test_orchestrator.py. Here we only verify the HTTP contract: a minimal,
    # valid request is accepted (200) and shaped into a compliant AuditResponse.
    # Returning an immediate stub keeps this route test deterministic and fast.
    from app.schemas.telemetry import AuditResponse, NokiaApiTelemetry

    async def fake_audit(request, progress_callback=None):
        return AuditResponse(
            msisdn=request.msisdn,
            amount=request.amount,
            transaction_type=request.transaction_type,
            risk_score="LOW",
            status="APPROVED",
            telemetry=NokiaApiTelemetry(),
            reasoning="stubbed route-contract test",
            recommended_action="approve",
        )

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
    assert payload["status"] == "APPROVED"
    assert payload["risk_score"] == "LOW"
    assert payload["telemetry"]["evidence_summary"] is not None


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
