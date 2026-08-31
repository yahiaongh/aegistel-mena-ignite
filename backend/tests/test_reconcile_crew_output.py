import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.crew_specialists import _reconcile_crew_output

# Regression guard: `recommended_action` is a required `str` on FinalAssessment
# and is validated at FinalAssessment(**specialist_output["assessment"]).
# _pick_value must never surface a null/None here (an audit would otherwise 500),
# so whatever the LLM emits, the reconciled output carries a usable value.


def _deterministic(**overrides):
    assessment = {
        "status": "STEP_UP_REQUIRED",
        "risk_score": "HIGH",
        "sim_swap_detected": True,
        "last_sim_swap_date": "2026-08-01",
        "location_verification_match": True,
        "roaming_status": "DOMESTIC",
        "roaming_country": None,
        "qod_session_active": False,
        "qod_profile": None,
        "reasoning": "deterministic reasoning",
        "recommended_action": "step_up",
    }
    assessment.update(overrides)
    return {"assessment": assessment}


def test_reconcile_fills_recommended_action_when_llm_emits_null():
    det = _deterministic()
    crew = dict(det["assessment"])
    crew["recommended_action"] = None
    reconciled, _ = _reconcile_crew_output(crew, det)
    assert reconciled["recommended_action"] == "step_up"


def test_reconcile_fills_recommended_action_when_llm_omits_key():
    det = _deterministic()
    crew = dict(det["assessment"])
    del crew["recommended_action"]
    reconciled, _ = _reconcile_crew_output(crew, det)
    assert reconciled["recommended_action"] == "step_up"


def test_reconcile_returns_deterministic_when_crew_emits_nothing():
    det = _deterministic()
    reconciled, reasons = _reconcile_crew_output({}, det)
    assert reconciled == det["assessment"]
    assert reconciled["recommended_action"] == "step_up"
    assert any("parseable" in r for r in reasons)
