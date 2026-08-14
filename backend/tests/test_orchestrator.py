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


def test_cross_border_risk_requires_actual_roaming_not_memory():
    # Memory alone must not flag cross-border risk: a clean domestic audit with
    # prior incident history should report cross_border_risk=False, since the
    # field now reflects the live roaming telemetry only.
    from app.agents.memory_agent import memory_engine

    memory_engine.clear_all_memory()
    memory_engine.record_incident(
        "+99999991001",
        "past domestic incident",
        {"status": "BLOCKED", "risk_score": "HIGH"},
    )

    request = AuditRequest(
        msisdn="+99999991001",
        amount=1000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=False,
    )

    result = asyncio.run(execute_audit(request))

    assert result.telemetry.roaming_status == "DOMESTIC"
    assert result.telemetry.cross_border_risk is False
    memory_engine.clear_all_memory()


def test_simulator_subscriber_is_immune_to_memory_poisoning():
    # Regression: the documented sandbox subscriber +99999991001 accumulated
    # HIGH/CRITICAL incidents during demo testing, which escalated every
    # subsequent clean audit into STEP_UP_REQUIRED via memory weighting. The
    # simulator subscribers are synthetic demo identities: their memory must be
    # excluded so the clean control case stays honest and repeatable.
    from app.agents.graph_orchestrator import SIMULATOR_MSISDNS
    from app.agents.memory_agent import memory_engine

    assert "+99999991001" in SIMULATOR_MSISDNS
    memory_engine.clear_all_memory()
    memory_engine.record_incident(
        "+99999991001",
        "polluted demo history",
        {"status": "BLOCKED", "risk_score": "CRITICAL"},
    )

    request = AuditRequest(
        msisdn="+99999991001",
        amount=1500.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=True,
    )

    result = asyncio.run(execute_audit(request))

    assert result.status == "APPROVED"
    assert result.risk_score == "LOW"
    memory_engine.clear_all_memory()


def test_simulator_subscriber_records_incidents_but_skips_weighting():
    # Simulator audits MUST still be recorded: the operator history panel is a
    # core feature and the demo numbers are exactly what gets audited. What is
    # excluded is memory-based verdict weighting (retrieval), so the recorded
    # trail never escalates a clean control case.
    from app.agents.memory_agent import memory_engine

    memory_engine.clear_all_memory()

    request = AuditRequest(
        msisdn="+99999991000",
        amount=30000.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.0, longitude=46.0),
        request_qod_slice=False,
    )

    result = asyncio.run(execute_audit(request))

    incidents = memory_engine.list_all_incidents("+99999991000")
    assert len(incidents) == 1
    assert incidents[0]["metadata"]["risk_score"] == result.risk_score
    assert incidents[0]["metadata"]["status"] == result.status

    # Weighting is still excluded: a polluted history for the clean control
    # subscriber does not change its verdict.
    memory_engine.record_incident(
        "+99999991001",
        "polluted demo history",
        {"status": "BLOCKED", "risk_score": "CRITICAL"},
    )
    clean = AuditRequest(
        msisdn="+99999991001",
        amount=1500.0,
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=True,
    )
    clean_result = asyncio.run(execute_audit(clean))
    assert clean_result.status == "APPROVED"
    assert clean_result.risk_score == "LOW"
    memory_engine.clear_all_memory()
