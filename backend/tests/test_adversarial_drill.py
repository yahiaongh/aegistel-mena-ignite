"""Adversarial Drill tests: the dynamic red-team lineup against the blue-team crew.

Runs the deterministic path (use_llm=False) so the tests never depend on
provider availability, rate limits or network timeouts. Lineup sampling is
seeded so expectations are pinned, and the tests assert that the attacker
lineup actually varies across runs.
"""

import random
import sys

import pytest

sys.path.insert(0, ".")

from app.agents.drill_agent import (  # noqa: E402
    _ARCHETYPES,
    _BLIND_SPOT_IDS,
    _sample_lineup,
    _validate_narration,
    THREAT_LEVELS,
    VALID_OUTCOMES,
    _grade,
    _outcome_for,
    _readiness_score,
    default_playbook,
    run_adversarial_drill,
)

VALID_VERDICTS = {"APPROVED", "STEP_UP_REQUIRED", "REJECTED", "BLOCKED", "MANUAL_REVIEW", "ERROR"}
EVIDENCE_MSISDNS = {"+99999991000", "+99999991001", "+99999991002", "+9999123456"}


def test_archetype_pool_is_valid_and_deep():
    assert len(_ARCHETYPES) >= 12
    seen_ids = set()
    for archetype in _ARCHETYPES:
        assert archetype["id"] not in seen_ids
        seen_ids.add(archetype["id"])
        assert archetype["threat_level"] in THREAT_LEVELS
        assert archetype["msisdn"] in EVIDENCE_MSISDNS
        lo, hi = archetype["amount_range"]
        assert 0 < lo < hi
        assert archetype["names"]
        assert archetype["intent"]
        assert archetype["transaction_types"]
        assert archetype["regions"]


def test_default_playbook_has_guaranteed_composition():
    plays = default_playbook()
    assert len(plays) == 6
    control = next(p for p in plays if p["archetype"] == "control")
    assert control["threat_level"] == "LOW"
    assert control["profile"]["msisdn"] == "+99999991001"
    assert control["profile"]["amount"] <= 500
    blind_ids = [p["id"] for p in plays if p["id"] in _BLIND_SPOT_IDS]
    assert len(blind_ids) >= 2
    heavy = [p for p in plays if p["threat_level"] in {"HIGH", "CRITICAL"}]
    assert len(heavy) >= 2
    assert len({p["archetype"] for p in plays}) == len(plays)
    for play in plays:
        assert play["profile"]["msisdn"] in EVIDENCE_MSISDNS
        assert play["profile"]["amount"] > 0


def test_sampler_lineups_vary_across_seeds():
    lineups = set()
    for seed in range(10):
        lineup = _sample_lineup(random.Random(seed))
        assert len(lineup) == 6
        assert len({p["id"] for p in lineup}) == 6
        lineups.add(tuple(sorted(p["id"] for p in lineup)))
    assert len(lineups) >= 4, "the attacker must rotate scenarios between runs"


def test_drill_scores_every_play_and_grades_0_100():
    report = run_adversarial_drill(use_llm=False, seed=7)
    assert report["total_plays"] == 6
    assert report["generated_by_llm"] is False
    assert report["lineup_source"] == "sampled"
    assert 0.0 <= report["readiness_score"] <= 100.0
    assert report["grade"] in {"A+", "A", "B", "C", "D"}
    assert sum(report["outcomes"].values()) == report["total_plays"]
    for play in report["plays"]:
        assert play["outcome"] in VALID_OUTCOMES
        assert play["verdict_status"] in VALID_VERDICTS
        assert play["defense_risk"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}
        if play["outcome"] in {"BLOCKED", "ESCALATED"}:
            assert len(play["detected_via"]) > 0


@pytest.mark.parametrize("seed", [3, 7, 11, 42])
def test_drill_finds_honest_blind_spots_and_variety(seed):
    """The differentiator claim: the drill must find a real weakness (not a
    perfect score) and the weakness must rotate with the lineup."""
    report = run_adversarial_drill(use_llm=False, seed=seed)
    control = next(p for p in report["plays"] if p["archetype"] == "control")
    assert control["outcome"] == "CLEARED"
    assert control["verdict_status"] == "APPROVED"
    for play in report["plays"]:
        if play["threat_level"] in {"HIGH", "CRITICAL"}:
            assert play["outcome"] in {"BLOCKED", "ESCALATED"}, play
    assert report["readiness_score"] < 100.0


def test_blind_spot_families_rotate_across_runs():
    seen = set()
    for seed in [3, 7, 11, 42]:
        report = run_adversarial_drill(use_llm=False, seed=seed)
        for blind in report["blind_spots"]:
            assert blind["outcome"] in {"PARTIALLY_MISSED", "MISSED"}
            assert blind["note"]
            seen.add(blind["play_id"])
    assert len(seen) >= 2, "different lineups must expose different blind spots"


def test_outcome_mapping_math():
    assert _outcome_for("CRITICAL", "REJECTED", "CRITICAL") == "BLOCKED"
    assert _outcome_for("HIGH", "STEP_UP_REQUIRED", "HIGH") == "ESCALATED"
    assert _outcome_for("MEDIUM", "STEP_UP_REQUIRED", "MEDIUM") == "ESCALATED"
    assert _outcome_for("LOW", "APPROVED", "LOW") == "CLEARED"
    assert _outcome_for("MEDIUM", "APPROVED", "LOW") == "PARTIALLY_MISSED"
    assert _outcome_for("HIGH", "APPROVED", "LOW") == "MISSED"
    assert _outcome_for("CRITICAL", "APPROVED", "LOW") == "MISSED"
    assert _outcome_for("HIGH", "UNKNOWN", "LOW") == "ERROR"


def test_readiness_weighting_and_grades():
    assert _readiness_score(["BLOCKED", "BLOCKED", "BLOCKED", "CLEARED"]) == 100.0
    assert _readiness_score(["MISSED"]) == 0.0
    assert _readiness_score(["BLOCKED", "MISSED"]) == 50.0
    assert _readiness_score(["ESCALATED", "ESCALATED"]) == 75.0
    assert _grade(95.0) == "A+"
    assert _grade(85.0) == "A"
    assert _grade(73.3) == "B"
    assert _grade(55.0) == "D"


def test_custom_playbook_accepted():
    custom = [default_playbook()[0]]
    report = run_adversarial_drill(plays=custom, use_llm=False)
    assert report["total_plays"] == 1
    assert report["plays"][0]["id"] == custom[0]["id"]
    assert report["plays"][0]["outcome"] in {"BLOCKED", "ESCALATED", "PARTIALLY_MISSED", "MISSED"}


def test_invalid_threat_level_clamped():
    heavy = next(p for p in default_playbook() if p["threat_level"] in {"HIGH", "CRITICAL"})
    heavy["threat_level"] = "GODLIKE"
    report = run_adversarial_drill(plays=[heavy], use_llm=False)
    assert report["plays"][0]["threat_level"] == "HIGH"
    assert report["plays"][0]["outcome"] in {"BLOCKED", "ESCALATED"}


def test_narration_fallback_when_llm_unavailable(monkeypatch):
    """If the Fraud Genie cannot produce a lineup, the drill still runs on the
    rotating sampler — never a hard failure."""
    monkeypatch.setattr("app.agents.drill_agent._llm_narrate", lambda: None)
    monkeypatch.setattr(
        "app.agents.drill_agent.run_specialist_crew",
        lambda profile, history, tools: {"assessment": {"status": "APPROVED", "risk_score": "LOW"}, "used_fallback": True},
    )
    report = run_adversarial_drill(use_llm=True, seed=5)
    assert report["generated_by_llm"] is False
    assert report["lineup_source"] == "sampled"
    assert report["total_plays"] == 6
    assert all(p["verdict_status"] in VALID_VERDICTS for p in report["plays"])


def test_narration_validation_grounds_picks():
    good = {
        "lineup": [
            {"id": "control", "name": "A", "intent": "b", "amount": 100.0, "transaction_type": "P2P_TRANSFER", "region": [46.7, 24.7]},
            {"id": "micro-staging", "name": "B", "intent": "c", "amount": 5000.0, "transaction_type": "P2P_TRANSFER", "region": [46.7, 24.7]},
            {"id": "otp-sim-swap", "name": "C", "intent": "d", "amount": 120000.0, "transaction_type": "SAME_DAY_WIRE", "region": [46.7, 24.7]},
            {"id": "cross-border-mule", "name": "D", "intent": "e", "amount": 80000.0, "transaction_type": "CROSS_BORDER_SWIFT", "region": [30.0, 30.0]},
            {"id": "congestion-strike", "name": "E", "intent": "f", "amount": 40000.0, "transaction_type": "WIRE_TRANSFER", "region": [55.3, 25.2]},
            {"id": "groomed-warm-line", "name": "F", "intent": "g", "amount": 90000.0, "transaction_type": "P2P_TRANSFER", "region": [55.3, 25.2]},
        ]
    }
    plays = _validate_narration(good)
    assert plays is not None
    assert len(plays) == 6
    assert any(p["archetype"] == "control" for p in plays)
    for play in plays:
        assert play["profile"]["msisdn"] in EVIDENCE_MSISDNS
        assert play["profile"]["amount"] > 0
    # grounded: amounts clamped into the archetype's allowed range
    otp = next(p for p in plays if p["id"] == "otp-sim-swap")
    assert 90000.0 <= otp["profile"]["amount"] <= 250000.0
    # grounded: transaction type restricted to the archetype's allowed set
    mule = next(p for p in plays if p["id"] == "cross-border-mule")
    assert mule["profile"]["transaction_type"] == "CROSS_BORDER_SWIFT"


def test_drill_never_hangs_when_llm_exceeds_play_budget(monkeypatch):
    """A play whose LLM crew grinds past the per-play budget must degrade to
    the deterministic engine instead of dangling the drill."""
    import time

    monkeypatch.setattr("app.agents.drill_agent._llm_narrate", lambda: None)
    monkeypatch.setattr("app.agents.drill_agent._PLAY_LLM_BUDGET_S", 2)

    def slow_crew(profile, history, tools):
        if not profile.get("force_deterministic"):
            time.sleep(5)
        return {"assessment": {"status": "APPROVED", "risk_score": "LOW"}, "used_fallback": True}

    monkeypatch.setattr("app.agents.drill_agent.run_specialist_crew", slow_crew)

    report = run_adversarial_drill(use_llm=True, seed=9)
    assert report["total_plays"] == 6
    assert all(p["used_fallback"] for p in report["plays"])
    assert all(p["outcome"] in {"CLEARED", "PARTIALLY_MISSED", "MISSED"} for p in report["plays"])


def test_narration_validation_rejects_bad_lineups():
    base = [
        {"id": "control", "name": "A", "intent": "b", "amount": 100.0, "transaction_type": "P2P_TRANSFER", "region": [46.7, 24.7]},
        {"id": "micro-staging", "name": "B", "intent": "c", "amount": 5000.0, "transaction_type": "P2P_TRANSFER", "region": [46.7, 24.7]},
        {"id": "otp-sim-swap", "name": "C", "intent": "d", "amount": 120000.0, "transaction_type": "SAME_DAY_WIRE", "region": [46.7, 24.7]},
        {"id": "cross-border-mule", "name": "D", "intent": "e", "amount": 80000.0, "transaction_type": "CROSS_BORDER_SWIFT", "region": [30.0, 30.0]},
        {"id": "congestion-strike", "name": "E", "intent": "f", "amount": 40000.0, "transaction_type": "WIRE_TRANSFER", "region": [55.3, 25.2]},
        {"id": "groomed-warm-line", "name": "F", "intent": "g", "amount": 90000.0, "transaction_type": "P2P_TRANSFER", "region": [55.3, 25.2]},
    ]
    assert _validate_narration({"lineup": base}) is not None
    bad_id = [dict(e, id="invented-scenario") for e in base]
    assert _validate_narration({"lineup": bad_id}) is None
    wrong_count = {"lineup": base[:5]}
    assert _validate_narration(wrong_count) is None
    no_control = [e for e in base if e["id"] != "control"]
    assert _validate_narration({"lineup": no_control}) is None
    assert _validate_narration({"lineup": "nonsense"}) is None
    assert _validate_narration(None) is None
