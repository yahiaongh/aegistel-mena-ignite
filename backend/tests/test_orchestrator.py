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
    # QoD is recommendation-only: the decision NEVER provisions a session
    # (chargeable resource), even for a risky transaction with request_qod_slice
    # = true. The recommendation is surfaced instead.
    assert result.telemetry.qod_session_active is False
    assert result.qod_recommended is True


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


def test_tool_success_flag_tracks_status_and_error():
    # Evidence integrity: a tool call that errored or returned a failing HTTP
    # status must be flagged as failed, exactly matching what the payload says,
    # so the verdict trail is never internally inconsistent.
    from app.agents.graph_orchestrator import _tool_succeeded

    assert _tool_succeeded({"name": "check_sim_swap", "swapped": False, "status_code": 200}) is True
    assert _tool_succeeded({"name": "check_sim_swap", "swapped": False, "status_code": 301}) is True
    assert _tool_succeeded({"name": "verify_number", "status_code": 200}) is True
    assert _tool_succeeded({"name": "verify_location", "verificationResult": "TRUE"}) is True

    assert _tool_succeeded({"name": "verify_number", "status_code": 401, "error": "Authorization header is missing"}) is False
    assert _tool_succeeded({"name": "check_sim_swap", "status_code": 503, "source": "LOCAL FALLBACK", "error": "tool execution failed"}) is False
    assert _tool_succeeded({"name": "check_roaming_status", "status_code": 500}) is False
    assert _tool_succeeded({"name": "check_roaming_status", "status_code": 400}) is False
    assert _tool_succeeded({"name": "check_roaming_status", "error": "timeout"}) is False


def test_plan_tool_calls_selective_by_transaction_type():
    from app.agents.crew_specialists import plan_tool_calls

    light = plan_tool_calls({"transaction_type": "P2P_TRANSFER", "amount": 100.0})
    assert light["low_touch"] is True
    assert set(light["required"]) == {
        "check_sim_swap", "verify_number", "verify_location", "check_device_reachability"
    }
    assert set(light["deferred"]) == {"check_roaming_status", "get_congestion_insights"}
    assert light["rationale"]

    wire = plan_tool_calls({"transaction_type": "WIRE_TRANSFER", "amount": 100.0})
    assert wire["low_touch"] is False
    assert "check_roaming_status" in wire["required"]
    assert wire["deferred"] == ["get_congestion_insights"]


def test_crewai_planner_can_select_only_policy_deferred_tools(monkeypatch):
    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class FakeTask:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class FakeCrew:
        def __init__(self, *args, **kwargs):
            pass

        def kickoff(self):
            return '{"selected_optional_tools":["check_roaming_status","not_allowed"],"rationale":"Cross-border payment context."}'

    from app.agents import crew_specialists

    monkeypatch.setattr(crew_specialists, "Agent", FakeAgent)
    monkeypatch.setattr(crew_specialists, "Task", FakeTask)
    monkeypatch.setattr(crew_specialists, "Crew", FakeCrew)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "test-key")

    policy = crew_specialists.plan_tool_calls({"transaction_type": "P2P_TRANSFER", "amount": 100.0})
    plan = crew_specialists._apply_agent_tool_plan(
        {"transaction_type": "P2P_TRANSFER", "amount": 100.0, "agent_planning": True}, policy
    )

    assert plan["planning_mode"] == "crewai"
    assert plan["initial_optional"] == ["check_roaming_status"]
    assert plan["deferred"] == ["get_congestion_insights"]
    assert set(plan["required"]) == set(policy["required"])


def test_planned_optional_signal_absence_does_not_escalate():
    from app.agents.crew_specialists import synthesize_specialist_assessment

    # Low-touch plan deliberately defers roaming/congestion. Their absence must
    # NOT trip the fault tolerant gate, which exists for REQUIRED evidence only.
    req = {"msisdn": "+966500000001", "amount": 100.0, "transaction_type": "P2P_TRANSFER", "request_qod": False}
    tool_results = [
        {"name": "check_sim_swap", "swapped": False, "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "verify_number", "devicePhoneNumberVerified": True, "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "verify_location", "verificationResult": "TRUE", "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "check_device_reachability", "reachabilityStatus": "REACHABLE", "status_code": 200, "source": "Nokia NaC SDK"},
    ]
    out = synthesize_specialist_assessment(
        req,
        tool_results,
        [],
        required_signals=["sim", "number_verification", "location", "reachability"],
        plan_detail="deferred roaming + congestion",
    )
    assert out["assessment"]["status"] == "APPROVED"
    assert out["assessment"]["risk_score"] == "LOW"
    planners = [t for t in out["trace"] if t["action"] == "TOOL_PLANNING"]
    assert planners and planners[0]["status"] == "PLANNED"
    assert planners[0]["agent"] == "Orchestration Planner"


def test_missing_required_signal_still_trips_failsafe_with_plan():
    from app.agents.crew_specialists import synthesize_specialist_assessment

    # WIRE_TRANSFER requires roaming in its plan: an absent roaming signal must
    # escalate exactly like before. A plan can never weaken the guardrail.
    req = {"msisdn": "+966500000001", "amount": 100.0, "transaction_type": "WIRE_TRANSFER", "request_qod": False}
    tool_results = [
        {"name": "check_sim_swap", "swapped": False, "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "verify_number", "devicePhoneNumberVerified": True, "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "verify_location", "verificationResult": "TRUE", "status_code": 200, "source": "Nokia NaC SDK"},
        {"name": "check_device_reachability", "reachabilityStatus": "REACHABLE", "status_code": 200, "source": "Nokia NaC SDK"},
    ]
    out = synthesize_specialist_assessment(
        req,
        tool_results,
        [],
        required_signals=["sim", "number_verification", "location", "roaming", "reachability"],
        plan_detail="full value-movement plan",
    )
    assert out["assessment"]["status"] in {"STEP_UP_REQUIRED", "MANUAL_REVIEW"}
    assert out["assessment"]["risk_score"] in {"HIGH", "CRITICAL"}
    assert any(t["action"] == "FAIL_SAFE_GATE" for t in out["trace"])
