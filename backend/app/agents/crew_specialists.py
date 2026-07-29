import logging
from typing import Dict, Any
from app.services.camara_client import camara_client
from app.agents.state import AuditState

logger = logging.getLogger(__name__)

def run_security_specialist(state: AuditState) -> Dict[str, Any]:
    """Agent 1: Telemetry analysis using Nokia NaC SIM Swap API."""
    msisdn = state["msisdn"]
    amount = state["amount"]
    
    sim_res = camara_client.check_sim_swap(msisdn=msisdn)
    is_swapped = sim_res.get("swapped", False)
    sim_cleared = not is_swapped

    if is_swapped:
        thought = (
            f"CRITICAL WARNING: Nokia NaC CAMARA SIM Swap check returned swapped=True for MSISDN {msisdn}. "
            f"The transfer of ${amount:.2f} carries elevated risk of account takeover fraud."
        )
        status = "FAILED"
    else:
        thought = (
            f"Nokia NaC CAMARA SIM Swap telemetry for MSISDN {msisdn} indicates no SIM swap events "
            f"(swapped=False). Account identity integrity confirmed."
        )
        status = "PASSED"

    trace_item = {
        "agent": "SecuritySpecialist",
        "action": "CAMARA_SIM_SWAP_CHECK",
        "thought": thought,
        "status": status,
        "detail": f"swapped={is_swapped} | source={sim_res.get('source')}"
    }

    return {
        "sim_swap_cleared": sim_cleared,
        "agent_trace": trace_item
    }


def run_network_qod_agent(state: AuditState) -> Dict[str, Any]:
    """Agent 2: Network validation using Nokia NaC Location Verification API."""
    msisdn = state["msisdn"]
    lat = state["location"]["latitude"]
    lon = state["location"]["longitude"]

    loc_res = camara_client.verify_location(msisdn=msisdn, latitude=lat, longitude=lon)
    res_str = loc_res.get("verificationResult", "FALSE")
    location_cleared = (res_str == "TRUE")

    if not location_cleared:
        thought = (
            f"LOCATION VERIFICATION FAILED: Coordinates ({lat}, {lon}) returned verificationResult='{res_str}'. "
            f"Reason: {loc_res.get('reason', 'Device unverified in expected coverage sector')}."
        )
        status = "FAILED"
    else:
        thought = (
            f"Nokia NaC CAMARA Location Verification returned 'TRUE' for coordinates ({lat}, {lon}). "
            f"Device positioning matches requested transaction region."
        )
        status = "PASSED"

    trace_item = {
        "agent": "NetworkQoDAgent",
        "action": "CAMARA_LOCATION_VERIFY",
        "thought": thought,
        "status": status,
        "detail": f"verificationResult={res_str} | source={loc_res.get('source')}"
    }

    return {
        "location_cleared": location_cleared,
        "agent_trace": trace_item
    }


def run_risk_auditor(state: AuditState) -> Dict[str, Any]:
    """Agent 3: Synthesizes final fraud decision based on network telemetry & business logic."""
    sim_ok = state["sim_swap_cleared"]
    loc_ok = state["location_cleared"]
    amount = state["amount"]

    # Decision Matrix
    if not sim_ok:
        status = "REJECTED"
        risk_score = "CRITICAL"
        reasoning = (
            f"REJECTED: Transaction of ${amount:,.2f} blocked. Nokia NaC SIM Swap detection flagged an unauthorized "
            f"SIM exchange on MSISDN {state['msisdn']}, indicating high risk of SIM-swap account takeover."
        )
    elif not loc_ok:
        status = "REJECTED"
        risk_score = "HIGH"
        reasoning = (
            f"REJECTED: Transaction of ${amount:,.2f} blocked. Nokia NaC Location Verification failed for device "
            f"at coordinates ({state['location']['latitude']}, {state['location']['longitude']})."
        )
    elif amount >= 100000.0:
        status = "MANUAL_REVIEW"
        risk_score = "MEDIUM"
        reasoning = (
            f"FLAGGED FOR MANUAL REVIEW: Telemetry (SIM_OK & LOC_OK) passed, but high transaction value "
            f"(${amount:,.2f}) exceeds auto-approval limits."
        )
    else:
        status = "APPROVED"
        risk_score = "LOW"
        reasoning = (
            f"APPROVED: Transaction of ${amount:,.2f} cleared. Telemetry confirmed valid SIM state and verified "
            f"device geographic positioning via Nokia Network as Code API."
        )

    trace_item = {
        "agent": "RiskAuditor",
        "action": "DECISION_SYNTHESIS_&_MEMORY_STORE",
        "thought": reasoning,
        "status": status,
        "detail": f"Risk Level: {risk_score} | Decision Synthesized"
    }

    return {
        "risk_score": risk_score,
        "status": status,
        "reasoning": reasoning,
        "agent_trace": trace_item
    }