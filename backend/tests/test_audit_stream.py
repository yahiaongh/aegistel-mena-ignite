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


def test_stream_error_event_on_failure(monkeypatch):
    async def boom(request, progress_callback=None, tenant_id=None):
        raise RuntimeError("quota exhausted")

    from app import main as main_module

    monkeypatch.setattr(main_module, "execute_audit", boom)

    frames = _stream_audit(
        {
            "msisdn": "+99999991000",
            "amount": 5000,
            "transaction_type": "WIRE_TRANSFER",
            "current_location": {"latitude": 24.7, "longitude": 46.7},
            "metadata": {"_force_deterministic": True},
        }
    )
    event_names = [name for name, _ in frames]
    assert "error" in event_names
    error_payload = next(payload for name, payload in frames if name == "error")
    assert error_payload["type"] == "RuntimeError"
    assert "quota exhausted" in error_payload["error"]

def test_plan_event_shows_plan_and_only_planned_tools_run():
    frames = _stream_audit(
        {
            "msisdn": "+99999991001",
            "amount": 100,
            "transaction_type": "P2P_TRANSFER",
            "current_location": {"latitude": 24.7, "longitude": 46.7},
            "metadata": {"_force_deterministic": True},
        }
    )
    progress_types = [payload.get("type") for name, payload in frames if name == "progress"]
    assert "plan" in progress_types
    plan = next(payload for name, payload in frames if name == "progress" and payload.get("type") == "plan")
    assert set(plan["deferred"]) == {"check_roaming_status", "get_congestion_insights"}
    assert "check_roaming_status" not in plan["required"]

    # A clean low-value convenience flow stays on its initial plan: no
    # expansion, no deferred roaming/congestion, no QoD step-up.
    assert "plan:expand" not in progress_types
    tool_names = [payload["tool"] for name, payload in frames if name == "progress" and payload.get("type") == "tool:done"]
    assert "check_roaming_status" not in tool_names
    assert "get_congestion_insights" not in tool_names
    assert "check_sim_swap" in tool_names
    assert "verify_number" in tool_names
    assert "create_qod_session" not in tool_names

    result = next(payload for name, payload in frames if name == "result")
    actions = {item["action"] for item in result["agent_trace"]}
    assert "POLICY_TOOL_PLANNING" in actions
    assert result["status"] == "APPROVED"
    assert result["risk_score"] == "LOW"


def test_plan_expands_to_deferred_signals_on_risk():
    frames = _stream_audit(
        {
            "msisdn": "+99999991000",
            "amount": 120000,
            "transaction_type": "WIRE_TRANSFER",
            "current_location": {"latitude": 24.7, "longitude": 46.7},
            "metadata": {"_force_deterministic": True},
        }
    )
    progress_types = [payload.get("type") for name, payload in frames if name == "progress"]
    assert "plan" in progress_types
    assert "plan:expand" in progress_types
    tool_names = [payload["tool"] for name, payload in frames if name == "progress" and payload.get("type") == "tool:done"]
    assert "check_roaming_status" in tool_names
    assert "get_congestion_insights" in tool_names
    # Risk is surfaced as a QoD RECOMMENDATION, not auto-provisioning.
    assert "qod:recommended" in progress_types
    assert "qod:start" not in progress_types
    result = next(payload for name, payload in frames if name == "result")
    assert result["status"] in {"STEP_UP_REQUIRED", "BLOCKED", "MANUAL_REVIEW"}
    assert result["qod_recommended"] is True
    assert result["telemetry"]["qod_session_active"] is False
