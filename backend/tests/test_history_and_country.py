from fastapi.testclient import TestClient

from app.agents.crew_specialists import synthesize_specialist_assessment
from app.agents.memory_agent import memory_engine
from app.main import app


def test_roaming_country_uses_iso_country_name() -> None:
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991000", "amount": 120000, "request_qod": False},
        [{"name": "roamingStatus", "roamingStatus": "INTERNATIONAL_ROAMING", "countryIsoCodes": ["HU"]}],
        [],
    )
    assert result["assessment"]["roaming_country"] == "Hungary"


def test_history_endpoint_returns_structured_incidents() -> None:
    memory_engine.clear_all_memory()
    memory_engine.record_incident(
        "+99999991000",
        "history record",
        {
            "status": "BLOCKED",
            "risk_score": "HIGH",
            "amount": 1500,
            "roaming_status": "INTERNATIONAL_ROAMING",
        },
    )

    client = TestClient(app)
    response = client.get("/api/v1/history/+99999991000?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["incidents"][0]["status"] == "BLOCKED"
    assert payload["incidents"][0]["risk_score"] == "HIGH"
    assert payload["incidents"][0]["amount"] == 1500
    assert payload["incidents"][0]["roaming_status"] == "INTERNATIONAL_ROAMING"
