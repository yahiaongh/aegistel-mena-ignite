import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.crew_specialists import _reconcile_crew_output, synthesize_specialist_assessment
from app.agents.graph_orchestrator import _compute_confidence
from app.agents.memory_agent import LOCAL_STORE_PATH, memory_engine


def test_reconcile_sanitizes_country_claim_when_roaming_country_empty():
    parsed = {
        "status": "REJECTED",
        "risk_score": "CRITICAL",
        "reasoning": "The device is roaming in Hungary and appears suspicious.",
        "recommended_action": "MANUAL_REVIEW",
        "qod_session_active": True,
    }
    deterministic = {
        "assessment": {
            "status": "STEP_UP_REQUIRED",
            "risk_score": "HIGH",
            "sim_swap_detected": False,
            "last_sim_swap_date": None,
            "location_verification_match": True,
            "roaming_status": "INTERNATIONAL_ROAMING",
            "roaming_country": None,
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "The location verification is FALSE and the device is roaming.",
            "recommended_action": "Escalate the payment with a QoD-assisted step-up and human review.",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    assert "Hungary" not in assessment["reasoning"]
    assert "unspecified country" in assessment["reasoning"]
    assert any("sanitized reasoning" in msg for msg in mismatch_reasons)


def test_reachability_status_is_built_from_tool_evidence():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991002", "amount": 120000, "request_qod": False},
        [{"name": "check_device_reachability", "reachabilityStatus": "DATA_AND_SMS", "source": "sandbox"}],
        [],
    )
    assert result["assessment"]["reachability_status"] == "DATA_AND_SMS"


def test_reconcile_sanitizes_invented_roaming_status_claim():
    parsed = {
        "status": "REJECTED",
        "risk_score": "CRITICAL",
        "reasoning": "The device is in DOMESTIC_ROAMING and should be reviewed.",
        "recommended_action": "MANUAL_REVIEW",
        "qod_session_active": True,
    }
    deterministic = {
        "assessment": {
            "status": "STEP_UP_REQUIRED",
            "risk_score": "HIGH",
            "sim_swap_detected": False,
            "last_sim_swap_date": None,
            "location_verification_match": True,
            "roaming_status": "INTERNATIONAL_ROAMING",
            "roaming_country": None,
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "The location verification is FALSE and the device is roaming.",
            "recommended_action": "Escalate the payment with a QoD-assisted step-up and human review.",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    assert "DOMESTIC_ROAMING" not in assessment["reasoning"]
    assert "INTERNATIONAL_ROAMING" in assessment["reasoning"]
    assert any("roaming_status" in msg for msg in mismatch_reasons)


def test_qod_active_is_grounded_from_tool_evidence():
    parsed = {
        "status": "APPROVED",
        "risk_score": "LOW",
        "qod_session_active": True,
        "reasoning": "The device is fine.",
        "recommended_action": "Allow the transaction.",
    }
    deterministic = {
        "assessment": {
            "status": "APPROVED",
            "risk_score": "LOW",
            "sim_swap_detected": False,
            "last_sim_swap_date": None,
            "location_verification_match": True,
            "location_accuracy_meters": 2000.0,
            "geofence_status": "VERIFIED",
            "roaming_status": "DOMESTIC",
            "roaming_country": None,
            "reachability_status": "DATA_ONLY",
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "The device is fine.",
            "recommended_action": "Allow the transaction.",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    assert assessment["qod_session_active"] is False
    assert any("qod_session_active" in msg for msg in mismatch_reasons)


def test_geofence_status_preserves_unknown_state():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991002", "amount": 120000, "request_qod": False},
        [{"name": "verify_location", "verificationResult": "UNKNOWN", "radius_meters": 2000, "source": "sandbox"}],
        [],
    )
    assert result["assessment"]["geofence_status"] == "UNKNOWN"


def test_empty_verification_result_is_unknown_risk():
    # A CAMARA error row (e.g. 404) yields an empty verificationResult. That is
    # missing data, not a clean match: it must be treated as UNKNOWN risk so a
    # device whose location could not be checked is never silently approved.
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991001", "amount": 100, "request_qod": False},
        [{"name": "verify_location", "verificationResult": "", "radius_meters": 5000, "status_code": 404, "source": "sandbox"}],
        [],
    )
    assert result["assessment"]["geofence_status"] == "UNKNOWN"
    assert result["assessment"]["location_verification_match"] is False
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"
    assert result["assessment"]["risk_score"] == "HIGH"


def test_none_verification_result_is_unknown_risk():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991001", "amount": 100, "request_qod": False},
        [{"name": "verify_location", "verificationResult": None, "radius_meters": 5000, "status_code": 502, "source": "sandbox"}],
        [],
    )
    assert result["assessment"]["geofence_status"] == "UNKNOWN"
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"


def test_local_memory_persists_to_disk_across_reload():
    if LOCAL_STORE_PATH.exists():
        LOCAL_STORE_PATH.unlink()
    memory_engine.clear_all_memory()

    memory_engine.record_incident(
        "+99999991001",
        "persisted reload test",
        {"status": "BLOCKED", "risk_score": "HIGH"},
    )

    reloaded_engine = memory_engine.__class__()
    incidents = reloaded_engine.list_all_incidents("+99999991001")

    assert len(incidents) == 1
    assert incidents[0]["text"] == "persisted reload test"
    assert LOCAL_STORE_PATH.exists()


def test_compute_confidence_increases_with_live_sdk_signals():
    fallback_only = [
        {"source": "Nokia CAMARA Sandbox"},
        {"source": "Nokia CAMARA Sandbox"},
        {"source": "Nokia CAMARA Sandbox"},
    ]
    mixed = [
        {"source": "Nokia NaC SDK"},
        {"source": "Nokia NaC SDK"},
        {"source": "Nokia CAMARA Sandbox"},
    ]
    assert _compute_confidence(fallback_only) < _compute_confidence(mixed)


def test_large_amount_alone_triggers_step_up():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991001", "amount": 500000, "request_qod": False},
        [{"name": "check_sim_swap", "swapped": False, "source": "sandbox"}, {"name": "verify_location", "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"}, {"name": "check_roaming_status", "roamingStatus": "DOMESTIC", "source": "sandbox"}],
        [],
    )
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"
    assert result["assessment"]["risk_score"] == "MEDIUM"


def test_unreachable_device_alone_triggers_step_up():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991003", "amount": 100, "request_qod": False},
        [
            {"name": "check_sim_swap", "swapped": False, "source": "sandbox"},
            {"name": "verify_location", "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"},
            {"name": "check_roaming_status", "roamingStatus": "DOMESTIC", "source": "sandbox"},
            {"name": "check_device_reachability", "reachabilityStatus": "UNREACHABLE", "source": "sandbox"},
        ],
        [],
    )
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"
    assert result["assessment"]["risk_score"] in {"MEDIUM", "HIGH", "CRITICAL"}


def test_roaming_plus_swap_escalates_beyond_step_up():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991000", "amount": 120000, "request_qod": False},
        [
            {"name": "check_sim_swap", "swapped": True, "last_sim_swap_date": "2026-08-12T00:00:00Z", "source": "sandbox"},
            {"name": "verify_location", "verificationResult": "FALSE", "radius_meters": 2000, "source": "sandbox"},
            {"name": "check_roaming_status", "roamingStatus": "INTERNATIONAL_ROAMING", "countryIsoCodes": ["US"], "source": "sandbox"},
            {"name": "check_device_reachability", "reachabilityStatus": "DATA_ONLY", "source": "sandbox"},
        ],
        [],
    )
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"
    assert result["assessment"]["risk_score"] in {"HIGH", "CRITICAL"}


def test_clean_signal_low_amount_approves():
    result = synthesize_specialist_assessment(
        {"msisdn": "+99999991001", "amount": 100, "request_qod": False},
        [
            {"name": "check_sim_swap", "swapped": False, "source": "sandbox"},
            {"name": "verify_location", "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"},
            {"name": "check_roaming_status", "roamingStatus": "DOMESTIC", "source": "sandbox"},
            {"name": "check_device_reachability", "reachabilityStatus": "DATA_ONLY", "source": "sandbox"},
        ],
        [],
    )
    assert result["assessment"]["status"] == "APPROVED"
    assert result["assessment"]["risk_score"] == "LOW"


def test_recurrence_memory_does_not_escalate_verdict():
    # Ensure stored memory does not by itself flip an otherwise-LOW verdict
    from app.agents.memory_agent import memory_engine

    msisdn = "+99999991001"
    if memory_engine:
        memory_engine.clear_all_memory()

    # First, a clean low-risk assessment should be APPROVED
    base = synthesize_specialist_assessment(
        {"msisdn": msisdn, "amount": 100, "request_qod": False},
        [
            {"name": "check_sim_swap", "swapped": False, "source": "sandbox"},
            {"name": "verify_location", "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"},
            {"name": "check_roaming_status", "roamingStatus": "DOMESTIC", "source": "sandbox"},
            {"name": "check_device_reachability", "reachabilityStatus": "DATA_ONLY", "source": "sandbox"},
        ],
        [],
    )
    assert base["assessment"]["status"] == "APPROVED"

    # Simulate an incident being recorded by the orchestrator
    memory_engine.record_incident(msisdn, "simulated past incident", {"status": "STEP_UP_REQUIRED", "risk_score": "HIGH"})

    # Re-run the deterministic synthesis with memory hits present
    mem = memory_engine.list_all_incidents(msisdn)
    second = synthesize_specialist_assessment(
        {"msisdn": msisdn, "amount": 100, "request_qod": False},
        [
            {"name": "check_sim_swap", "swapped": False, "source": "sandbox"},
            {"name": "verify_location", "verificationResult": "TRUE", "radius_meters": 2000, "source": "sandbox"},
            {"name": "check_roaming_status", "roamingStatus": "DOMESTIC", "source": "sandbox"},
            {"name": "check_device_reachability", "reachabilityStatus": "DATA_ONLY", "source": "sandbox"},
        ],
        mem,
    )

    # Memory should not have escalated the deterministic verdict
    assert second["assessment"]["status"] == "APPROVED"


def test_llm_cannot_downgrade_deterministic_verdict():
    # Simulate a deterministic STEP_UP and an LLM that returns APPROVED/LOW.
    parsed = {"status": "APPROVED", "risk_score": "LOW", "reasoning": "LLM says OK"}
    deterministic = {
        "assessment": {
            "status": "STEP_UP_REQUIRED",
            "risk_score": "HIGH",
            "sim_swap_detected": False,
            "last_sim_swap_date": None,
            "location_verification_match": True,
            "roaming_status": "DOMESTIC",
            "roaming_country": None,
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "Deterministic step-up due to amount.",
            "recommended_action": "Step-up",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    # Final assessment must not be more lenient than deterministic
    assert assessment["status"] == "STEP_UP_REQUIRED"
    assert assessment["risk_score"] == "HIGH"
    assert any("downgrade" in msg or "lower risk_score" in msg for msg in mismatch_reasons)


def test_llm_cannot_invent_risk_on_clean_case():
    # Deterministic APPROVED (clean signal, sub-threshold amount) with an LLM
    # that hallucinates an escalation. The reconcile layer must cap it.
    parsed = {
        "status": "STEP_UP_REQUIRED",
        "risk_score": "HIGH",
        "reasoning": "The device shows SIM swap signs and should be step-upped.",
        "recommended_action": "Escalate with step-up.",
        "qod_session_active": False,
    }
    deterministic = {
        "assessment": {
            "status": "APPROVED",
            "risk_score": "LOW",
            "sim_swap_detected": False,
            "last_sim_swap_date": None,
            "location_verification_match": True,
            "location_accuracy_meters": 2000.0,
            "geofence_status": "VERIFIED",
            "roaming_status": "DOMESTIC",
            "roaming_country": None,
            "reachability_status": "DATA_ONLY",
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "Specialist synthesis found no strong compromise indicators and approved the transaction for the supplied network context.",
            "recommended_action": "Allow the transaction and continue monitoring for additional telemetry.",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    # A clean case must never be escalated just because the LLM ran.
    assert assessment["status"] == "APPROVED"
    assert assessment["risk_score"] == "LOW"
    # Judgment text must stay coherent with the forced APPROVED verdict.
    assert "no strong compromise indicators" in assessment["reasoning"]
    assert any("escalate" in msg for msg in mismatch_reasons)


def test_llm_may_intensify_confirmed_risk():
    # Deterministic STEP_UP (grounded risk), LLM goes stricter -> REJECTED.
    # The stricter-on-confirmed-risk capability must be preserved.
    parsed = {
        "status": "REJECTED",
        "risk_score": "CRITICAL",
        "reasoning": "SIM swap is confirmed; rejecting the transaction.",
        "recommended_action": "Reject and block.",
        "qod_session_active": True,
    }
    deterministic = {
        "assessment": {
            "status": "STEP_UP_REQUIRED",
            "risk_score": "HIGH",
            "sim_swap_detected": True,
            "last_sim_swap_date": "2026-08-12T00:00:00Z",
            "location_verification_match": False,
            "roaming_status": "INTERNATIONAL_ROAMING",
            "roaming_country": None,
            "qod_session_active": False,
            "qod_profile": None,
            "reasoning": "SIM swap evidence was present.",
            "recommended_action": "Escalate the payment with a QoD-assisted step-up and human review.",
        }
    }

    assessment, mismatch_reasons = _reconcile_crew_output(parsed, deterministic)

    # LLM chasing a REJECTED verdict on a confirmed-risk case is allowed.
    assert assessment["status"] == "REJECTED"
    assert assessment["risk_score"] == "CRITICAL"
    # No escalation/hallucination warnings for a grounded-risk row.
    assert not any("escalate" in msg for msg in mismatch_reasons)
