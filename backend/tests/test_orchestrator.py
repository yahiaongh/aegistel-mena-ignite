import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.graph_orchestrator import execute_audit
from app.schemas.telemetry import AuditRequest, LocationInput


def test_execute_audit_returns_structured_response():
    request = AuditRequest(
        msisdn="+9999123456",
        amount=50000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=True,
    )

    result = asyncio.run(execute_audit(request))
    print(result)
    assert result.status in {"APPROVED", "REJECTED", "BLOCKED", "STEP_UP_REQUIRED", "MANUAL_REVIEW"}
    assert result.risk_score in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert result.telemetry is not None
    assert result.agent_trace


def test_demo_msisdn_exposes_documented_risk_signals():
    request = AuditRequest(
        msisdn="+99999991000",
        amount=120000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.0, longitude=46.0),
        request_qod_slice=True,
    )

    result = asyncio.run(execute_audit(request))

    assert result.telemetry.sim_swap_detected is True
    assert result.telemetry.location_verification_match is False
    assert result.telemetry.qod_session_active is True


def test_fallback_reasoning_and_trace_are_contextual():
    request = AuditRequest(
        msisdn="+99999991000",
        amount=120000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.0, longitude=46.0),
        request_qod_slice=True,
    )

    result = asyncio.run(execute_audit(request))

    assert "SIM swap" in result.reasoning or "location" in result.reasoning.lower()
    assert all(step.thought != "Selected a CAMARA network capability based on the transaction context." for step in result.agent_trace)
    assert sum(1 for step in result.agent_trace if step.agent == "Autonomous_LLM_Orchestrator") <= 2


def test_specialist_agents_are_reflected_in_the_trace():
    request = AuditRequest(
        msisdn="+99999991000",
        amount=120000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.0, longitude=46.0),
        request_qod_slice=True,
    )

    result = asyncio.run(execute_audit(request))

    agent_names = {step.agent for step in result.agent_trace}
    assert "Security Specialist" in agent_names
    assert "Network Intelligence Specialist" in agent_names
