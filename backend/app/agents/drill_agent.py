"""Adversarial Drill — the same multi-agent engine playing both hats.

The defense pipeline used for live audits is exactly what the drill attacks.
A red-team playbook (Fraud Genie scenarios) is executed against the blue-team
crew; the report scores defense readiness, grades the engine, and lists the
blind spots the drill actually discovered.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .crew_specialists import run_specialist_crew

logger = logging.getLogger(__name__)

THREAT_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_OUTCOMES = {"BLOCKED", "ESCALATED", "PARTIALLY_MISSED", "MISSED", "CLEARED", "ERROR"}

_OUTCOME_WEIGHTS = {
    "BLOCKED": 1.0,
    "ESCALATED": 0.75,
    "CLEARED": 1.0,
    "PARTIALLY_MISSED": 0.4,
    "MISSED": 0.0,
    "ERROR": 0.0,
}


def _play(
    play_id: str,
    name: str,
    archetype: str,
    intent: str,
    msisdn: str,
    amount: float,
    threat_level: str,
    longitude: float = 46.7,
    latitude: float = 24.7,
    transaction_type: str = "P2P_TRANSFER",
    request_qod: bool = False,
    enforce_roaming_policy: bool = False,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    threat_level = threat_level.upper()
    if threat_level not in THREAT_LEVELS:
        threat_level = "HIGH"
    return {
        "id": play_id,
        "name": name,
        "archetype": archetype,
        "intent": intent,
        "threat_level": threat_level,
        "profile": {
            "msisdn": msisdn,
            "amount": amount,
            "longitude": longitude,
            "latitude": latitude,
            "request_qod": request_qod,
            "transaction_type": transaction_type,
            "metadata": {"enforce_roaming_policy": enforce_roaming_policy},
        },
        "history": history or [],
    }


def default_playbook() -> List[Dict[str, Any]]:
    """Six adversarial plays covering identity takeover, laundering, crowd-
    synchronized strikes, social engineering, staged micro-attacks and a
    clean control run. Curated so the drill always returns a verdict even
    when no LLM provider is available."""
    return [
        _play(
            "play-01",
            "OTP Intercept via SIM Swap",
            "identity_takeover",
            "Attacker swaps the victim SIM, intercepts the OTP and moves a high-value wire while the handset reports clearance.",
            "+99999991000", 120000.0, "CRITICAL",
            transaction_type="WIRE_TRANSFER",
        ),
        _play(
            "play-02",
            "Cross-Border Mule Relay",
            "money_laundering",
            "A mule in a conflict-zone corridor receives funds on an internationally roaming line with a hollow location match.",
            "+99999991000", 80000.0, "HIGH",
            longitude=30.0, latitude=30.0,
            enforce_roaming_policy=True,
        ),
        _play(
            "play-03",
            "Congestion-Synchronized Strike",
            "crowd_exploitation",
            "Strike timed to a mass event: attacker moves money while High cell congestion buries anomaly detection noise.",
            "+99999991000", 35000.0, "HIGH",
            transaction_type="WIRE_TRANSFER",
        ),
        _play(
            "play-04",
            "Warm-Line Burst (Groomed Account)",
            "social_engineering",
            "A groomed account with one prior incident attempts a large transfer from an otherwise clean line.",
            "+99999991001", 90000.0, "MEDIUM",
            history=[
                {"metadata": {"risk_score": "MEDIUM", "status": "STEP_UP_REQUIRED", "amount": 12000.0}}
            ],
        ),
        _play(
            "play-05",
            "Micro-Staging First Strike",
            "staged_attack",
            "Attacker keeps the first strike under the amount threshold and on a clean line to stage future fraud.",
            "+99999991001", 5000.0, "MEDIUM",
        ),
        _play(
            "play-06",
            "Clean-Line Control Run",
            "control",
            "A legitimate low-value transfer on a trusted line — the drill must not manufacture risk.",
            "+99999991001", 100.0, "LOW",
        ),
    ]


def _outcome_for(threat_level: str, status: str, risk_score: str) -> str:
    if status in {"REJECTED", "BLOCKED", "MANUAL_REVIEW"}:
        return "BLOCKED"
    if status == "STEP_UP_REQUIRED":
        return "ESCALATED"
    if status == "APPROVED":
        if threat_level == "LOW":
            return "CLEARED"
        if threat_level == "MEDIUM":
            return "PARTIALLY_MISSED"
        return "MISSED"
    return "ERROR"


def _detected_via(assessment: Dict[str, Any], history: List[Dict[str, Any]]) -> List[str]:
    flags: List[str] = []
    if assessment.get("sim_swap_detected"):
        flags.append("SIM SWAP")
    nv = assessment.get("number_verification_status")
    if nv and nv != "VERIFIED":
        flags.append("NUMBER VERIFICATION")
    if assessment.get("geofence_status") not in (None, "VERIFIED"):
        flags.append("GEOFENCE")
    if assessment.get("roaming_status") == "INTERNATIONAL_ROAMING":
        flags.append("ROAMING")
    if str(assessment.get("reachability_status", "")).upper() == "UNREACHABLE":
        flags.append("REACHABILITY")
    if str(assessment.get("max_congestion_level", "")).lower() == "high":
        flags.append("CONGESTION")
    if history:
        flags.append(f"MEMORY x{len(history)}")
    if not flags:
        flags.append("AMOUNT THRESHOLD")
    return flags


def _grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _readiness_score(outcomes: List[str]) -> float:
    if not outcomes:
        return 0.0
    return round(100.0 * sum(_OUTCOME_WEIGHTS.get(o, 0.0) for o in outcomes) / len(outcomes), 1)


def _execute_play(play: Dict[str, Any], use_llm: bool) -> Dict[str, Any]:
    profile = dict(play.get("profile") or {})
    profile["force_deterministic"] = not use_llm
    history = play.get("history") or []
    threat = str(play.get("threat_level", "HIGH")).upper()
    if threat not in THREAT_LEVELS:
        threat = "HIGH"
    try:
        crew_result = run_specialist_crew(profile, history, [])
        assessment = crew_result.get("assessment") or {}
        status = str(assessment.get("status", "UNKNOWN")).upper()
        risk_score = str(assessment.get("risk_score", "UNKNOWN")).upper()
        outcome = _outcome_for(threat, status, risk_score)
        detected_via = _detected_via(assessment, history) if outcome in {"BLOCKED", "ESCALATED"} else []
        return {
            "id": play.get("id", "play-?"),
            "name": play.get("name", "Unnamed play"),
            "archetype": play.get("archetype", "unknown"),
            "intent": play.get("intent", ""),
            "threat_level": threat,
            "verdict_status": status,
            "defense_risk": risk_score,
            "outcome": outcome,
            "detected_via": detected_via,
            "used_fallback": bool(crew_result.get("used_fallback")),
        }
    except Exception as exc:  # noqa: BLE001 — a play that crashes the crew is itself a finding
        logger.exception("Drill play %s failed: %s", play.get("id"), exc)
        return {
            "id": play.get("id", "play-?"),
            "name": play.get("name", "Unnamed play"),
            "archetype": play.get("archetype", "unknown"),
            "intent": play.get("intent", ""),
            "threat_level": threat,
            "verdict_status": "ERROR",
            "defense_risk": "UNKNOWN",
            "outcome": "ERROR",
            "detected_via": [],
            "used_fallback": True,
        }


def run_adversarial_drill(
    plays: Optional[List[Dict[str, Any]]] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Execute the red-team playbook against the blue-team crew and grade the defense.

    Plays are independent, so they run concurrently (bounded worker pool) — a
    full LLM drill finishes well under the Next.js dev proxy deadline.
    """
    playbook = plays if plays is not None else default_playbook()

    workers = min(3, len(playbook)) if use_llm else len(playbook)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        play_results = list(pool.map(lambda play: _execute_play(play, use_llm), playbook))

    outcomes = [play["outcome"] for play in play_results]

    score = _readiness_score(outcomes)
    outcome_counts = {name: outcomes.count(name) for name in VALID_OUTCOMES if outcomes.count(name) > 0}

    blind_spots = [
        {
            "play_id": result["id"],
            "play_name": result["name"],
            "threat_level": result["threat_level"],
            "outcome": result["outcome"],
            "note": (
                "The defense approved this play despite a MEDIUM threat: every network signal read "
                "clean and the amount fell under the step-up threshold."
                if result["outcome"] == "PARTIALLY_MISSED"
                else "The defense approved this play at HIGH or CRITICAL threat — no signal fired."
            ),
        }
        for result in play_results
        if result["outcome"] in {"MISSED", "PARTIALLY_MISSED"}
    ]

    recommendations: List[str] = []
    if any(b["outcome"] == "PARTIALLY_MISSED" for b in blind_spots):
        recommendations.append(
            "Blind spot: first-strike micro-attacks on clean lines. Consider adaptive amount thresholds "
            "driven by subscriber tenure, device age and congestion history instead of a flat limit."
        )
    if any(b["outcome"] == "MISSED" for b in blind_spots):
        recommendations.append(
            "Blind spot: full compromise missed. A staggering check against memory across sibling "
            "MSISDNs would surface the mule pattern before the final wire."
        )
    if not blind_spots:
        recommendations.append("No blind spots discovered in this playbook. Expand the playbook with "
                               "a wider MSISDN population to keep the drill honest.")

    return {
        "drill_id": f"drill-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
        "generated_by_llm": False,
        "playbook": f"{len(playbook)} curated adversarial plays",
        "readiness_score": score,
        "grade": _grade(score),
        "total_plays": len(playbook),
        "outcomes": outcome_counts,
        "plays": play_results,
        "blind_spots": blind_spots,
        "recommendations": recommendations,
    }