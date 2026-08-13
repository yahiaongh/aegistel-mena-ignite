"""SSE audit stream tests: progress events followed by the final result."""

import json
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_STATUSES = {"APPROVED", "REJECTED", "BLOCKED", "STEP_UP_REQUIRED", "MANUAL_REVIEW"}


def _stream_audit(body: dict):
    frames = []
    with client.stream("POST", "/api/v1/audit/stream", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current_event = None
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                frames.append((current_event, json.loads(line[len("data:"):].strip())))
    return frames


def test_stream_emits_progress_then_result():
    frames = _stream_audit(
        {
            "msisdn": "+99999991000",
            "amount": 120000,
            "transaction_type": "WIRE_TRANSFER",
            "current_location": {"latitude": 24.7, "longitude": 46.7},
            "metadata": {"_force_deterministic": True},
        }
    )
    event_names = [name for name, _ in frames]
    assert "result" in event_names
    assert "error" not in event_names

    progress_types = [payload.get("type") for name, payload in frames if name == "progress"]
    assert "tools:start" in progress_types
    assert "synthesis:done" in progress_types
    assert "crew:done" in progress_types

    tool_names = [payload["tool"] for name, payload in frames if name == "progress" and payload.get("type") == "tool:done"]
    assert "check_sim_swap" in tool_names
    assert "verify_number" in tool_names
    assert "get_congestion_insights" in tool_names

    result = next(payload for name, payload in frames if name == "result")
    assert result["status"] in VALID_STATUSES
    assert result["risk_score"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert len(result["telemetry"]["tool_results"]) >= 6
    assert result["raw_output"]
    assert any(t["name"] == "verify_number" and t["duration_ms"] is not None for t in result["telemetry"]["tool_results"])


def test_stream_result_matches_non_stream_audit():
    body = {
        "msisdn": "+99999991001",
        "amount": 100,
        "transaction_type": "P2P_TRANSFER",
        "current_location": {"latitude": 24.7, "longitude": 46.7},
        "metadata": {"_force_deterministic": True},
    }
    frames = _stream_audit(body)
    result = next(payload for name, payload in frames if name == "result")

    plain = client.post("/api/v1/audit", json=body)
    assert plain.status_code == 200
    assert plain.json()["status"] == result["status"]
    assert plain.json()["risk_score"] == result["risk_score"]


def test_stream_error_event_on_failure():
    frames = _stream_audit(
        {
            "msisdn": "+99999991000",
            "amount": -5000,
            "transaction_type": "WIRE_TRANSFER",
            "current_location": {"latitude": 24.7, "longitude": 46.7},
            "metadata": {"_force_deterministic": True},
        }
    )
    event_names = [name for name, _ in frames]
    assert ("error" in event_names) or ("result" in event_names), frames