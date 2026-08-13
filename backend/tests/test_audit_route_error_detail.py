import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_module


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
