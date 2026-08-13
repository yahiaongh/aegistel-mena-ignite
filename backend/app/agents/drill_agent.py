"""Adversarial Drill — the same multi-agent engine playing both hats.

The defense pipeline used for live audits is exactly what the drill attacks.
A red-team playbook (Fraud Genie scenarios) is executed against the blue-team
crew; the report scores defense readiness, grades the engine, and lists the
blind spots the drill actually discovered.

The attacker is dynamic: every run draws a fresh lineup from an archetype
arsenal (14 scenarios). When an LLM provider is available, the Fraud Genie
curates the lineup — fresh names, intents, amounts and regions for this run.
Otherwise a seeded sampler rotates scenarios deterministically. Either way,
every play is grounded on real Nokia sandbox evidence profiles, so verdicts
stay honest.
"""

import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .crew_specialists import (
    MODEL_CHAIN,
    _model_in_cooldown,
    _model_provider_available,
    run_specialist_crew,
)

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

# Evidence profiles: MSISDNs are Nokia's documented sandbox lines whose
# behaviors produce stable telemetry for the crew to read.
_EVIDENCE_MSISDNS = {"+99999991000", "+99999991001", "+99999991002", "+9999123456"}


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


# --------------------------------------------------------------------------
# Archetype arsenal. Each archetype is a scenario generator: the sampler (or
# the Fraud Genie) picks one, then realization fills in name, amount, region
# and transaction type. Grounded rules (below the catalog) guarantee that a
# play's telemetry always matches the MSISDN's real documented behavior.
# --------------------------------------------------------------------------
_RIYADH = (46.7, 24.7)
_CAIRO = (30.0, 30.0)
_DUBAI = (55.3, 25.2)

_ARCHETYPES: List[Dict[str, Any]] = [
    {
        "id": "otp-sim-swap",
        "archetype": "identity_takeover",
        "threat_level": "CRITICAL",
        "msisdn": "+99999991000",
        "amount_range": (90000.0, 250000.0),
        "transaction_types": ["WIRE_TRANSFER", "SAME_DAY_WIRE"],
        "regions": [_RIYADH, _DUBAI],
        "names": [
            "OTP Intercept via SIM Swap",
            "Silent Swap, High-Value Wire",
            "Verification Bypass Move",
        ],
        "intent": "Attacker swaps the victim SIM, intercepts the OTP and moves a high-value wire while the handset still reports clearance.",
        "history": None,
    },
    {
        "id": "cross-border-mule",
        "archetype": "money_laundering",
        "threat_level": "HIGH",
        "msisdn": "+99999991000",
        "amount_range": (60000.0, 150000.0),
        "transaction_types": ["CROSS_BORDER_SWIFT"],
        "regions": [_CAIRO, _RIYADH],
        "names": [
            "Cross-Border Mule Relay",
            "Conflict-Zone Corridor Transfer",
            "Roaming Mule Settlement",
        ],
        "intent": "A mule in a conflict-zone corridor receives funds on an internationally roaming line with a hollow location match.",
        "history": None,
        "enforce_roaming_policy": True,
    },
    {
        "id": "congestion-strike",
        "archetype": "crowd_exploitation",
        "threat_level": "HIGH",
        "msisdn": "+99999991000",
        "amount_range": (30000.0, 90000.0),
        "transaction_types": ["WIRE_TRANSFER"],
        "regions": [_DUBAI, _CAIRO],
        "names": [
            "Congestion-Synchronized Strike",
            "Mass-Event Timing Play",
            "Noise-Buried Wire",
        ],
        "intent": "Strike timed to a mass event: attacker moves money while High cell congestion buries anomaly detection noise.",
        "history": None,
    },
    {
        "id": "vip-clearance-wire",
        "archetype": "extreme_amount",
        "threat_level": "CRITICAL",
        "msisdn": "+99999991000",
        "amount_range": (300000.0, 600000.0),
        "transaction_types": ["SAME_DAY_WIRE"],
        "regions": [_DUBAI, _RIYADH],
        "names": [
            "VIP-Clearance Same-Day Wire",
            "Priority Corridor Escalation",
            "High-Net-Worth Fast Cash-Out",
        ],
        "intent": "Attacker masquerades as a VIP account to move a same-day wire beyond normal authorization limits.",
        "history": None,
    },
    {
        "id": "gift-card-laundering",
        "archetype": "laundering_gift_card",
        "threat_level": "HIGH",
        "msisdn": "+99999991000",
        "amount_range": (5000.0, 20000.0),
        "transaction_types": ["GIFT_CARD_TOPUP"],
        "regions": [_RIYADH, _DUBAI],
        "names": [
            "Gift-Card Wash Cycle",
            "Small-Ticket Topup Relay",
            "Retail-Card Laundering Loop",
        ],
        "intent": "Attacker launders proceeds through retail gift-card top-ups on a compromised line, exploiting high-risk small-amount lanes.",
        "history": None,
    },
    {
        "id": "mule-relay",
        "archetype": "mule_relay",
        "threat_level": "HIGH",
        "msisdn": "+99999991000",
        "amount_range": (40000.0, 120000.0),
        "transaction_types": ["P2P_TRANSFER", "WIRE_TRANSFER"],
        "regions": [_CAIRO, _DUBAI],
        "names": [
            "Mule Relay Hop",
            "Two-Hop Settlement Chain",
            "Split-and-Forward Pattern",
        ],
        "intent": "Funds hop through a chain of mule lines, each piece small enough to dodge single-transfer scrutiny.",
        "history": None,
    },
    {
        "id": "groomed-warm-line",
        "archetype": "social_engineering",
        "threat_level": "MEDIUM",
        "msisdn": "+99999991001",
        "amount_range": (60000.0, 150000.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_DUBAI, _RIYADH],
        "names": [
            "Warm-Line Burst (Groomed Account)",
            "Trusted-Line Verification Dance",
            "One-Incident Cashing-Out",
        ],
        "intent": "A groomed account with one prior incident attempts a large transfer from an otherwise clean line.",
        "history": [{"metadata": {"risk_score": "MEDIUM", "status": "STEP_UP_REQUIRED", "amount": 12000.0}}],
    },
    {
        "id": "repeat-offender",
        "archetype": "repeat_offender",
        "threat_level": "HIGH",
        "msisdn": "+99999991001",
        "amount_range": (30000.0, 80000.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_RIYADH, _DUBAI],
        "names": [
            "Repeat Offender Return",
            "Memory-Matched Re-Entry",
            "Rebound After Rejection",
        ],
        "intent": "A line with a high-severity rejection in memory returns with a mid-size transfer, betting the memory faded.",
        "history": [{"metadata": {"risk_score": "HIGH", "status": "REJECTED", "amount": 40000.0}}],
    },
    {
        "id": "unverifiable-line",
        "archetype": "unknown_line_probe",
        "threat_level": "HIGH",
        "msisdn": "+9999123456",
        "amount_range": (40000.0, 90000.0),
        "transaction_types": ["WIRE_TRANSFER"],
        "regions": [_CAIRO, _DUBAI],
        "names": [
            "Unverifiable Line Transfer",
            "No-Verification Wire",
            "Ghost-Device Settlement",
        ],
        "intent": "Number Verification cannot bind the device; the attacker leans on the ambiguity to push a large transfer through.",
        "history": None,
    },
    {
        "id": "silent-hijack",
        "archetype": "silent_hijack",
        "threat_level": "MEDIUM",
        "msisdn": "+9999123456",
        "amount_range": (15000.0, 30000.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_DUBAI, _CAIRO],
        "names": [
            "Silent Line Hijack",
            "Unbound-Device Extraction",
            "Quiet Takeover Drawdown",
        ],
        "intent": "A line with no documented swap and a moderate amount — the quiet middle of the attack graph.",
        "history": None,
    },
    # --- blind-spot archetypes: clean or muted telemetry that the verdict
    #     engine reads as APPROVED at non-LOW threat -------------------------
    {
        "id": "micro-staging",
        "archetype": "staged_attack",
        "threat_level": "MEDIUM",
        "msisdn": "+99999991001",
        "amount_range": (1000.0, 24000.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_RIYADH, _DUBAI],
        "names": [
            "Micro-Staging First Strike",
            "Sub-Threshold Probe",
            "Clean-Line Test Transfer",
        ],
        "intent": "Attacker keeps the first strike under the amount threshold and on a clean line to stage future fraud.",
        "history": None,
    },
    {
        "id": "mid-size-window",
        "archetype": "mid_size_window",
        "threat_level": "MEDIUM",
        "msisdn": "+99999991001",
        "amount_range": (25000.0, 99000.0),
        "transaction_types": ["P2P_TRANSFER", "WIRE_TRANSFER"],
        "regions": [_RIYADH, _CAIRO],
        "names": [
            "Mid-Size Cash-Out Window",
            "QoD-Provisioned Transfer",
            "Twenty-Five-to-Ninety-Nine Play",
        ],
        "intent": "Attacker targets the band between the QoD tripwire and the hard amount step-up, where provisioning happens but approval still clears.",
        "history": None,
    },
    {
        "id": "congestion-medium-window",
        "archetype": "congestion_window",
        "threat_level": "MEDIUM",
        "msisdn": "+99999991002",
        "amount_range": (5000.0, 24000.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_DUBAI, _RIYADH],
        "names": [
            "Medium-Congestion Window",
            "Muted-Network Drawdown",
            "Corroboration-Only Timing",
        ],
        "intent": "The attacker times the move to a Medium-congestion window where the network's own signal is muted.",
        "history": None,
    },
    {
        "id": "control",
        "archetype": "control",
        "threat_level": "LOW",
        "msisdn": "+99999991001",
        "amount_range": (50.0, 500.0),
        "transaction_types": ["P2P_TRANSFER"],
        "regions": [_RIYADH],
        "names": [
            "Clean-Line Control Run",
            "Trusted Low-Value Transfer",
        ],
        "intent": "A legitimate low-value transfer on a trusted line — the drill must not manufacture risk.",
        "history": None,
    },
]

_BLIND_SPOT_IDS = {"micro-staging", "mid-size-window", "congestion-medium-window"}
_HEAVY_IDS = [a["id"] for a in _ARCHETYPES if a["threat_level"] in {"HIGH", "CRITICAL"}]

# Wall-clock budget for a single LLM-run play: if the crew chain cannot finish
# in time (rate limits, slow providers), the play degrades to the deterministic
# engine instead of dangling the drill. Budget math: narration <= 60s, then
# 6 plays / 3 workers x 45s = 90s -> worst case ~150s, under the endpoint
# (180s) and dev proxy (300s) ceilings.
_PLAY_LLM_BUDGET_S = 45


def _realize(archetype: Dict[str, Any], rng: random.Random, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    o = overrides or {}
    lo, hi = archetype["amount_range"]
    amount = float(o.get("amount")) if o.get("amount") is not None else rng.uniform(lo, hi)
    amount = round(max(lo, min(hi, amount)), 2)
    region = o.get("region") if o.get("region") else rng.choice(archetype["regions"])
    tx = o.get("transaction_type") or rng.choice(archetype["transaction_types"])
    name = str(o.get("name") or rng.choice(archetype["names"]))
    intent = str(o.get("intent") or archetype["intent"])
    return _play(
        archetype["id"],
        name,
        archetype["archetype"],
        intent,
        archetype["msisdn"],
        amount,
        archetype["threat_level"],
        longitude=region[0],
        latitude=region[1],
        transaction_type=tx,
        enforce_roaming_policy=bool(archetype.get("enforce_roaming_policy")),
        history=archetype.get("history"),
    )


def _sample_lineup(rng: random.Random, count: int = 6) -> List[Dict[str, Any]]:
    """Rotating lineup with structural guarantees: always a clean control run,
    always at least two blind-spot-prone scenarios, always at least two
    heavy-signal plays, no archetype repeated."""
    by_id = {a["id"]: a for a in _ARCHETYPES}
    picks: List[Dict[str, Any]] = [by_id["control"]]
    picks += [by_id[i] for i in rng.sample(sorted(_BLIND_SPOT_IDS), min(2, len(_BLIND_SPOT_IDS)))]
    heavy_pick_n = min(2, max(0, count - len(picks)))
    picks += [by_id[i] for i in rng.sample(sorted(_HEAVY_IDS), heavy_pick_n)]
    remaining = count - len(picks)
    if remaining > 0:
        picked_ids = {p["id"] for p in picks}
        filler = [a["id"] for a in _ARCHETYPES if a["id"] not in picked_ids]
        picks += [by_id[i] for i in rng.sample(filler, min(len(filler), remaining))]
    picks = picks[:count]
    rng.shuffle(picks)
    return [_realize(a, rng) for a in picks]


def default_playbook() -> List[Dict[str, Any]]:
    """Backward-compatible alias: a fixed seeded lineup."""
    return _sample_lineup(random.Random(7))


# --------------------------------------------------------------------------
# Fraud Genie narration — one LLM call to curate this run's lineup. Any
# failure or malformed output falls back to the deterministic sampler, so the
# drill always runs.
# --------------------------------------------------------------------------
_NARRATOR_PROMPT = """You are the "Fraud Genie", the red-team scenario designer for a telecom fraud defense engine.

Below is the arsenal of attack scenarios. Each lists its scenario id, threat level, the real sandbox MSISDN whose documented network behavior produces its telemetry, the allowed amount range, allowed transaction types and allowed regions (lon/lat).

Rules for the lineup you must curate:
- Pick exactly 6 scenarios, each with a DISTINCT id, and invent for each a FRESH attack name and a one-line intent (concrete, believable, different from run to run).
- The "control" scenario MUST be included (it keeps the drill honest).
- Include at least 2 scenarios whose amounts stay under 100,000 (these probe the approval thresholds).
- Include at least 2 high-signal scenarios from the MSISDN +99999991000 family (SIM-swap evidence).
- Never use the same scenario id twice; never invent scenario ids.
- Choose amounts inside each scenario's allowed range; choose a transaction type from its allowed list and a region from its allowed list.

Reply with JSON ONLY (no prose, no markdown fences):
{"lineup": [{"id": "...", "name": "...", "intent": "...", "amount": 12345.67, "transaction_type": "...", "region": [lon, lat]}, ...]}"""


def _llm_narrate() -> Optional[List[Dict[str, Any]]]:
    model = next(
        (m for m in MODEL_CHAIN["specialist"] if _model_provider_available(m) and not _model_in_cooldown(m)),
        None,
    )
    if model is None:
        return None
    try:
        from crewai import Agent, Crew, Process, Task

        catalog = [
            {
                "id": a["id"],
                "threat_level": a["threat_level"],
                "msisdn": a["msisdn"],
                "amount_range": [int(a["amount_range"][0]), int(a["amount_range"][1])],
                "transaction_types": a["transaction_types"],
                "regions": a["regions"],
            }
            for a in _ARCHETYPES
        ]
        narrator = Agent(
            role="Fraud Genie - red-team scenario designer",
            goal="Curate a fresh, grounded adversarial lineup for this drill run",
            backstory="You dream up believable telecom fraud scenarios, always pinned to the real evidence each sandbox line emits.",
            llm=model,
            max_iter=1,
            max_execution_time=45,
            cache=False,
            verbose=False,
        )
        task = Task(
            description=f"{_NARRATOR_PROMPT}\n\nARSENAL:\n{json.dumps(catalog)}",
            expected_output="A JSON object with a 'lineup' key containing exactly 6 play objects.",
            agent=narrator,
        )
        crew = Crew(agents=[narrator], tasks=[task], process=Process.sequential, verbose=False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = str(pool.submit(crew.kickoff).result(timeout=60))
        parsed = _parse_narration(result)
        lineup = _validate_narration(parsed)
        if lineup is None:
            logger.warning("Fraud Genie narration was invalid; falling back to sampled lineup")
            return None
        return lineup
    except Exception as exc:  # noqa: BLE001 — narration is best-effort
        logger.warning("Fraud Genie narration failed (%s); falling back to sampled lineup", exc)
        return None


def _parse_narration(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _validate_narration(parsed: Any) -> Optional[List[Dict[str, Any]]]:
    """Ground the Fraud Genie's picks: ids must exist, amounts must stay in
    range, count must be exactly 6 and control must be present."""
    if not isinstance(parsed, dict):
        return None
    lineup = parsed.get("lineup")
    if not isinstance(lineup, list) or len(lineup) != 6:
        return None
    by_id = {a["id"]: a for a in _ARCHETYPES}
    seen: set[str] = set()
    plays: List[Dict[str, Any]] = []
    for entry in lineup:
        if not isinstance(entry, dict):
            return None
        eid = entry.get("id")
        if eid not in by_id or eid in seen:
            return None
        seen.add(eid)
        lo, hi = by_id[eid]["amount_range"]
        try:
            amount = float(entry.get("amount"))
            tx = str(entry.get("transaction_type") or by_id[eid]["transaction_types"][0])
            region = entry.get("region")
        except (TypeError, ValueError):
            return None
        if tx not in by_id[eid]["transaction_types"]:
            tx = by_id[eid]["transaction_types"][0]
        if (
            not isinstance(region, (list, tuple))
            or len(region) != 2
            or region not in by_id[eid]["regions"]
        ):
            region = by_id[eid]["regions"][0]
        plays.append(
            _realize(
                by_id[eid],
                random.Random(),
                {
                    "name": entry.get("name"),
                    "intent": entry.get("intent"),
                    "amount": max(lo, min(hi, amount)),
                    "transaction_type": tx,
                    "region": region,
                },
            )
        )
    if not any(p["archetype"] == "control" for p in plays):
        return None
    return plays


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


def _blind_spot_note(result: Dict[str, Any]) -> str:
    archetype = str(result.get("archetype", ""))
    if result["outcome"] == "PARTIALLY_MISSED":
        notes = {
            "staged_attack": "The defense approved a sub-threshold first strike on a clean line: no signal fired and the amount never crossed the $25k QoD / $100k risk tripwires.",
            "mid_size_window": "The defense approved a mid-size transfer ($25k-$99k) on a clean line: QoD was provisioned at the $25k tripwire but no signal fired, so the verdict stayed APPROVED.",
            "congestion_window": "The defense approved a transfer during Medium congestion: Medium is corroboration-only in the risk engine, so this window carries no signal at all.",
        }
        return notes.get(archetype, "The defense approved this play despite a MEDIUM threat: every network signal read clean and the amount fell under the step-up threshold.")
    return "The defense approved this play at HIGH or CRITICAL threat — no signal fired."


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
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute this run's red-team lineup against the blue-team crew and grade the defense.

    The lineup is re-curated every run: the Fraud Genie LLM shapes it when a
    provider is available; otherwise a seeded sampler rotates scenarios. Plays
    are independent and run concurrently (bounded worker pool).
    """
    curated_by_llm = False
    if plays is not None:
        playbook = plays
        lineup_source = "custom"
    else:
        narrated = _llm_narrate() if use_llm else None
        if narrated is not None:
            playbook = narrated
            curated_by_llm = True
            lineup_source = "fraud-genie"
        else:
            playbook = _sample_lineup(random.Random(seed) if seed is not None else random.Random())
            lineup_source = "sampled"

    workers = min(3, len(playbook)) if use_llm else len(playbook)
    play_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_execute_play, play, use_llm): play for play in playbook}
        for future, play in futures.items():
            try:
                play_results.append(future.result(timeout=_PLAY_LLM_BUDGET_S if use_llm else None))
            except FutureTimeoutError:
                # The LLM chain is grinding against provider limits — never let
                # the demo hang. Re-run this play through the deterministic
                # engine (the same grounded verdict path the tests pin).
                logger.warning("Drill play %s exceeded the %ss LLM budget; re-running deterministically", play.get("id"), _PLAY_LLM_BUDGET_S)
                future.cancel()
                rescued = _execute_play(play, use_llm=False)
                rescued["used_fallback"] = True
                play_results.append(rescued)
    play_results.sort(key=lambda result: next((i for i, p in enumerate(playbook) if p["id"] == result["id"]), 0))

    outcomes = [play["outcome"] for play in play_results]

    score = _readiness_score(outcomes)
    outcome_counts = {name: outcomes.count(name) for name in VALID_OUTCOMES if outcomes.count(name) > 0}

    blind_spots = [
        {
            "play_id": result["id"],
            "play_name": result["name"],
            "threat_level": result["threat_level"],
            "outcome": result["outcome"],
            "note": _blind_spot_note(result),
        }
        for result in play_results
        if result["outcome"] in {"MISSED", "PARTIALLY_MISSED"}
    ]

    blind_families = {str(b["play_id"]) for b in blind_spots}
    recommendations: List[str] = []
    if "micro-staging" in blind_families:
        recommendations.append(
            "Blind spot: first-strike micro-attacks (under $25k) on clean lines pass untouched. "
            "Consider adaptive per-subscriber amount thresholds driven by tenure and device age "
            "instead of a flat limit."
        )
    if "mid-size-window" in blind_families:
        recommendations.append(
            "Blind spot: mid-size transfers ($25k-$99k) on clean lines are approved while QoD is "
            "provisioned. Make the provisioning tripwire actionable — surface QoD provisioning as a "
            "first-class step-up signal at $25k, not just an observation."
        )
    if "congestion-medium-window" in blind_families:
        recommendations.append(
            "Blind spot: Medium-congestion windows carry no verdict weight. In high-threat corridors, "
            "let Medium congestion act as corroboration for amount-based step-ups."
        )
    if any(b["outcome"] == "MISSED" for b in blind_spots):
        recommendations.append(
            "Blind spot: full compromise missed. A staggering check against memory across sibling "
            "MSISDNs would surface the mule pattern before the final wire."
        )
    if not blind_spots:
        recommendations.append("No blind spots discovered in this lineup. Expand the arsenal with "
                               "a wider MSISDN population to keep the drill honest.")

    return {
        "drill_id": f"drill-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
        "generated_by_llm": curated_by_llm,
        "lineup_source": lineup_source,
        "playbook": f"{len(playbook)}-play lineup ({lineup_source}{' · Fraud Genie' if curated_by_llm else ''})",
        "readiness_score": score,
        "grade": _grade(score),
        "total_plays": len(playbook),
        "outcomes": outcome_counts,
        "plays": play_results,
        "blind_spots": blind_spots,
        "recommendations": recommendations,
    }