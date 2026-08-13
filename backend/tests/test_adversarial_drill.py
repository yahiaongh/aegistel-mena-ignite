"""Adversarial Drill tests: the red-team playbook against the blue-team crew.

Runs the deterministic path (use_llm=False) so the tests never depend on
provider availability, rate limits or network timeouts.
"""

import sys

import pytest

sys.path.insert(0, ".")

from app.agents.drill_agent import (  # noqa: E402
    THREAT_LEVELS,
    VALID_OUTCOMES,
    _grade,
    _outcome_for,
    _readiness_score,
    default_playbook,
    run_adversarial_drill,
)

VALID_VERDICTS = {"APPROVED", "STEP_UP_REQUIRED", "REJECTED", "BLOCKED", "MANUAL_REVIEW", "ERROR"}


def test_default_playbook_is_valid():
    plays = default_playbook()
    assert len(plays) >= 5
    for play in plays:
        assert play["threat_level"] in THREAT_LEVELS
        assert play["profile"]["msisdn"].startswith("+9999")
        assert play["profile"]["amount"] >= 0


def test_drill_scores_every_play_and_grades_0_100():
    report = run_adversarial_drill(use_llm=False)
    assert report["total_plays"] == len(default_playbook())
    assert 0.0 <= report["readiness_score"] <= 100.0
    assert report["grade"] in {"A+", "A", "B", "C", "D"}
    assert sum(report["outcomes"].values()) == report["total_plays"]
    for play in report["plays"]:
        assert play["outcome"] in VALID_OUTCOMES
        assert play["verdict_status"] in VALID_VERDICTS
        assert play["defense_risk"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}
        if play["outcome"] in {"BLOCKED", "ESCALATED"}:
            assert len(play["detected_via"]) > 0


def test_drill_discovers_micro_staging_blind_spot():
    """The differentiator claim: the drill must find a real weakness, not a perfect score."""
    report = run_adversarial_drill(use_llm=False)
    stager = next((p for p in report["plays"] if p["id"] == "play-05"), None)
    assert stager is not None
    assert stager["outcome"] == "PARTIALLY_MISSED"
    assert any(b["play_id"] == "play-05" for b in report["blind_spots"])
    assert any("Micro-Staging" in b["play_name"] for b in report["blind_spots"])


def test_drill_control_run_never_manufactures_risk():
    report = run_adversarial_drill(use_llm=False)
    control = next((p for p in report["plays"] if p["id"] == "play-06"), None)
    assert control is not None
    assert control["outcome"] == "CLEARED"
    assert control["verdict_status"] == "APPROVED"
    assert control["defense_risk"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}


def test_heavy_fraud_plays_are_blocked_or_escalated():
    report = run_adversarial_drill(use_llm=False)
    for play in report["plays"]:
        if play["threat_level"] in {"HIGH", "CRITICAL"}:
            assert play["outcome"] in {"BLOCKED", "ESCALATED"}, play


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
    assert report["plays"][0]["id"] == "play-01"
    assert report["plays"][0]["outcome"] in {"BLOCKED", "ESCALATED", "PARTIALLY_MISSED", "MISSED"}


def test_invalid_threat_level_clamped():
    plays = default_playbook()
    plays[0]["threat_level"] = "GODLIKE"
    report = run_adversarial_drill(plays=plays, use_llm=False)
    assert report["plays"][0]["threat_level"] == "HIGH"
    assert report["plays"][0]["outcome"] in {"BLOCKED", "ESCALATED"}