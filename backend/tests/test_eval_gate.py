import pytest
from app.agents.crew_specialists import synthesize_specialist_assessment

# Reuse the same scenarios used by tests/test_behavioral_eval.py
SCENARIOS = [
    {"msisdn": "+99999991000", "amount": 120000.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991001", "amount": 100.0, "expected": "APPROVED"},
    {"msisdn": "+99999991000", "amount": 100.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991001", "amount": 25000.0, "expected": "APPROVED"},
    {"msisdn": "+99999991000", "amount": 99999.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991001", "amount": 100000.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991000", "amount": 50000.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991001", "amount": 50000.0, "expected": "APPROVED"},
    {"msisdn": "+99999991000", "amount": 120000.0, "expected": "STEP_UP_REQUIRED"},
    {"msisdn": "+99999991001", "amount": 99999.0, "expected": "APPROVED"},
]


def _build_tool_results_for(msisdn: str, amount: float, request_qod: bool):
    """Return deterministic tool payloads matching the documented Nokia sandbox behavior."""
    if msisdn.endswith("1000"):
        # documented problematic number
        sim = {"swapped": True, "last_sim_swap_date": "2026-08-01T00:00:00+00:00", "source": "Nokia NaC SDK", "name": "check_sim_swap"}
        verify = {"verificationResult": "FALSE", "radius_meters": 5000, "source": "Nokia NaC SDK", "name": "verify_location"}
        roam = {"roamingStatus": "INTERNATIONAL_ROAMING", "roaming": True, "countryIsoCodes": ["HU"], "source": "Nokia NaC SDK", "name": "check_roaming_status"}
        reach = {"reachabilityStatus": "SMS_ONLY", "reachable": True, "connectivity": ["SMS"], "source": "Nokia NaC SDK", "name": "check_device_reachability"}
    else:
        sim = {"swapped": False, "source": "Nokia NaC SDK", "name": "check_sim_swap"}
        verify = {"verificationResult": "TRUE", "radius_meters": 5000, "source": "Nokia NaC SDK", "name": "verify_location"}
        roam = {"roamingStatus": "DOMESTIC", "roaming": False, "countryIsoCodes": [], "source": "Nokia NaC SDK", "name": "check_roaming_status"}
        reach = {"reachabilityStatus": "DATA_ONLY", "reachable": True, "connectivity": ["DATA"], "source": "Nokia NaC SDK", "name": "check_device_reachability"}

    results = [sim, verify, roam]
    if amount >= 25000 or request_qod:
        results.append({"sessionId": "test-session", "qosStatus": "REQUESTED", "qosProfile": "QOS_E", "name": "create_qod_session"})
    results.append(reach)
    return results


def test_eval_gate_matches_deterministic_scenarios():
    failures = []
    for scen in SCENARIOS:
        req = {"msisdn": scen["msisdn"], "amount": scen["amount"], "transaction_type": "WIRE_TRANSFER", "request_qod": False}
        tool_results = _build_tool_results_for(scen["msisdn"], scen["amount"], False)
        out = synthesize_specialist_assessment(req, tool_results, [], enforce_roaming_policy=False)
        status = out["assessment"]["status"]
        if status != scen["expected"]:
            failures.append((scen, status))
    assert not failures, f"Deterministic eval gate mismatches: {failures}"


def test_transaction_type_high_risk_escalates():
    # A clean subscriber that would be APPROVED for WIRE_TRANSFER
    msisdn = "+99999991001"
    req_wire = {"msisdn": msisdn, "amount": 100.0, "transaction_type": "WIRE_TRANSFER", "request_qod": False}
    req_cross = {"msisdn": msisdn, "amount": 100.0, "transaction_type": "CROSS_BORDER_SWIFT", "request_qod": False}
    tool_results = _build_tool_results_for(msisdn, 100.0, False)

    out_wire = synthesize_specialist_assessment(req_wire, tool_results, [], enforce_roaming_policy=False)
    out_cross = synthesize_specialist_assessment(req_cross, tool_results, [], enforce_roaming_policy=False)

    assert out_wire["assessment"]["status"] == "APPROVED", "WIRE_TRANSFER should remain neutral for this benign number"
    assert out_cross["assessment"]["status"] == "STEP_UP_REQUIRED", "CROSS_BORDER_SWIFT should escalate the otherwise benign number"

    # Reasoning must be meaningful: not empty, not the bare 'Specialist synthesis identified ' prefix,
    # and should mention the transaction type or indicate high risk.
    reasoning = out_cross["assessment"].get("reasoning", "") or ""
    assert reasoning and not reasoning.strip().endswith("Specialist synthesis identified"), f"Reasoning was empty or incomplete: {reasoning!r}"
    assert (
        "CROSS_BORDER_SWIFT" in reasoning.upper() or "HIGH RISK" in reasoning.upper() or "TRANSACTION TYPE" in reasoning.upper()
    ), f"Reasoning should mention transaction type or high risk context: {reasoning!r}"