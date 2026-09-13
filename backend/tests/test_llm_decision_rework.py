import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.agents.crew_specialists as cs
from app.agents.graph_orchestrator import execute_audit
from app.schemas.telemetry import AuditRequest, LocationInput


def _hermetic_llm_env(monkeypatch, verdict_json, verify_number_ok=True):
    """Enable provider credentials and substitute an in-process fake CrewAI
    backend so the LLM planning and crew-adjudication paths run without any
    network egress. The fake crew always returns `verdict_json`."""
    monkeypatch.setattr(cs.settings, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(cs.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(cs.settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(cs.settings, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(cs, "_reachable_providers", lambda: {"groq": True})

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

    class FakeTask:
        def __init__(self, *args, **kwargs):
            pass

    class FakeCrew:
        def __init__(self, *args, **kwargs):
            pass

        def kickoff(self):
            return verdict_json

    monkeypatch.setattr(cs, "Agent", FakeAgent)
    monkeypatch.setattr(cs, "Task", FakeTask)
    monkeypatch.setattr(cs, "Crew", FakeCrew)

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        if tool_name == "check_sim_swap":
            return {"name": tool_name, "swapped": False, "source": "sandbox"}
        if tool_name == "verify_location":
            return {"name": tool_name, "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"}
        if tool_name == "check_roaming_status":
            return {"name": tool_name, "roamingStatus": "DOMESTIC", "source": "sandbox"}
        if tool_name == "check_device_reachability":
            return {"name": tool_name, "reachabilityStatus": "DATA_ONLY", "source": "sandbox"}
        if tool_name == "verify_number":
            if not verify_number_ok:
                return {"name": tool_name, "status_code": 503, "source": "LOCAL FALLBACK", "error": "tool execution failed"}
            return {"name": tool_name, "verified": True, "devicePhoneNumberVerified": True, "verificationStatus": "VERIFIED", "status_code": 200, "source": "sandbox"}
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(cs, "_run_tool_payload", fake_run_tool_payload)


def _request(msisdn="+15551234567", amount=100.0, tx="P2P_TRANSFER"):
    return AuditRequest(
        msisdn=msisdn,
        amount=amount,
        transaction_type=tx,
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=False,
    )


def test_llm_verdict_survives_graph_end_to_end(monkeypatch):
    # The LLM escalates a nominally clean case to STEP_UP/HIGH. Under the old
    # no-escalation ceiling the reconcile layer forced it back to APPROVED;
    # under the LLM-first contract it is the final verdict.
    _hermetic_llm_env(
        monkeypatch,
        '{"status": "STEP_UP_REQUIRED", "risk_score": "HIGH", '
        '"reasoning": "The subscriber lost silent device verification recently (LLM-adjudicated).", '
        '"recommended_action": "Escalate with step-up verification."}',
    )

    result = asyncio.run(execute_audit(_request(), tenant_id="e2e-llm-decides"))

    assert result.used_fallback is False
    assert result.status == "STEP_UP_REQUIRED"
    assert result.risk_score == "HIGH"
    assert result.qod_recommended is True


def test_verifier_routes_contradictory_llm_verdict_to_manual_review(monkeypatch):
    # The LLM claims APPROVED with a HIGH risk score. That is self-contradictory,
    # so the verifier node coerces it to manual review rather than auto-approving.
    _hermetic_llm_env(
        monkeypatch,
        '{"status": "APPROVED", "risk_score": "HIGH", '
        '"reasoning": "The transaction is clean but warrants caution.", '
        '"recommended_action": "Allow the transaction."}',
    )

    result = asyncio.run(execute_audit(_request(), tenant_id="e2e-verifier-coherence"))

    assert result.used_fallback is False
    assert result.status == "MANUAL_REVIEW"
    assert "coherence gate" in result.reasoning


def test_fail_safe_floor_holds_through_graph(monkeypatch):
    # Identity evidence could not be gathered (verify_number errored), so the
    # fail-safe gate locked a non-approval verdict. The LLM trying to approve
    # must not cancel the fail-safe invariant, and the LLM path still ran.
    _hermetic_llm_env(
        monkeypatch,
        '{"status": "APPROVED", "risk_score": "LOW", '
        '"reasoning": "No risk indicators found; approving.", '
        '"recommended_action": "Allow the transaction."}',
        verify_number_ok=False,
    )

    result = asyncio.run(execute_audit(_request(), tenant_id="e2e-failsafe-floor"))

    assert result.used_fallback is False
    assert result.status in {"STEP_UP_REQUIRED", "MANUAL_REVIEW"}
    assert result.risk_score in {"HIGH", "MEDIUM"}