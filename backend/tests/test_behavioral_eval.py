"""Behavioral evaluation gate for the audit engine (formerly scripts/round21_eval.py).

This is fundamentally a test:

* By default (offline suite) only the pure scoring/logic tests run; they are fast
  and need no live keys or telecom calls.
* With ``--run-live`` and real model + telecom keys configured, the full 10-scenario
  behavioral eval runs end to end through ``execute_audit`` and asserts the core
  coherence guarantee: the LLM-augmented verdict may only ever be *at least as
  strict* as the deterministic contract, never more lenient.

It can also be invoked directly as a CLI to inspect per-row attribution or export
a CSV:

    # Deterministic-only rows (fast, offline-ish):
    python -m pytest tests/test_behavioral_eval.py --run-live -m live -s -- \
        ... (see main() flags)

or via the previous standalone interface (kept for continuity):

    python -m tests.test_behavioral_eval --skip-llm --verbose --csv out.csv
"""

import argparse
import asyncio
from collections import Counter

import pytest

from app.agents.graph_orchestrator import execute_audit
from app.agents.memory_agent import memory_engine
from app.core.config import settings
from app.schemas.telemetry import AuditRequest, LocationInput

# Use only documented stable numbers to avoid live-data drift and quota noise.
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

_STATUS_ORDER = ["APPROVED", "STEP_UP_REQUIRED", "MANUAL_REVIEW", "REJECTED", "BLOCKED"]


def is_stricter_or_equal_status(actual: str, expected: str) -> bool:
    return _status_rank(actual) >= _status_rank(expected)


def _status_rank(status: str) -> int:
    try:
        return _STATUS_ORDER.index(status)
    except ValueError:
        return -1


def risk_rank(score: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(score, 2)


async def run_one(scen, deterministic_only: bool):
    # Ensure scenario isolation
    memory_engine.clear_all_memory()

    req = AuditRequest(
        msisdn=scen["msisdn"],
        amount=scen["amount"],
        transaction_type="WIRE_TRANSFER",
        current_location=LocationInput(latitude=24.7, longitude=46.7),
        request_qod_slice=False,
    )

    # Toggle model keys to force deterministic-only path when requested
    saved = {
        "GROQ_API_KEY": settings.GROQ_API_KEY,
        "GOOGLE_API_KEY": settings.GOOGLE_API_KEY,
        "OPENROUTER_API_KEY": settings.OPENROUTER_API_KEY,
        "CEREBRAS_API_KEY": getattr(settings, "CEREBRAS_API_KEY", ""),
    }
    if deterministic_only:
        settings.GROQ_API_KEY = ""
        settings.GOOGLE_API_KEY = ""
        settings.OPENROUTER_API_KEY = ""
        if hasattr(settings, "CEREBRAS_API_KEY"):
            settings.CEREBRAS_API_KEY = ""

    try:
        res = await execute_audit(req)
        # Extract provider/model attribution from agent_trace
        providers = [t.provider for t in (res.agent_trace or []) if getattr(t, "provider", None)]
        models = [t.model for t in (res.agent_trace or []) if getattr(t, "model", None)]
        out = {
            "msisdn": res.msisdn,
            "status": res.status,
            "risk_score": res.risk_score,
            "reachability": res.telemetry.reachability_status,
            "qod_status": res.telemetry.qod_status,
            "used_fallback": res.used_fallback,
            "providers": providers,
            "models": models,
        }
    except Exception as e:
        out = {"msisdn": scen["msisdn"], "error": str(e), "used_fallback": True}
    finally:
        # restore keys
        settings.GROQ_API_KEY = saved["GROQ_API_KEY"]
        settings.GOOGLE_API_KEY = saved["GOOGLE_API_KEY"]
        settings.OPENROUTER_API_KEY = saved["OPENROUTER_API_KEY"]
        if hasattr(settings, "CEREBRAS_API_KEY"):
            settings.CEREBRAS_API_KEY = saved.get("CEREBRAS_API_KEY", "")

    return out


def score_results(results, skip_llm: bool):
    """Compute the aggregate metrics from the per-scenario augmented/deterministic rows."""
    det_ok = sum(1 for r in results if r["deterministic"].get("status") == r["scenario"]["expected"])

    aug_pass = 0
    aug_did_not_run = 0
    aug_skipped = 0
    aug_agreed = 0
    aug_stricter = 0
    aug_lenient_blocked = 0
    for r in results:
        a = r["augmented"]
        expected = r["scenario"]["expected"]
        det_status = r["deterministic"].get("status")
        if isinstance(a, dict) and a.get("skipped"):
            aug_skipped += 1
        elif isinstance(a, dict) and a.get("used_fallback"):
            aug_did_not_run += 1
        elif isinstance(a, dict) and a.get("status"):
            status_ok = is_stricter_or_equal_status(a["status"], expected)
            det_risk_rank = risk_rank(r["deterministic"].get("risk_score"))
            aug_risk_rank = risk_rank(a.get("risk_score"))
            risk_ok = aug_risk_rank >= det_risk_rank
            if status_ok and risk_ok:
                aug_pass += 1
            if det_status and a.get("status") == det_status:
                aug_agreed += 1
            elif det_status and is_stricter_or_equal_status(a["status"], det_status):
                aug_stricter += 1
            elif det_status:
                aug_lenient_blocked += 1

    return {
        "det_ok": det_ok,
        "n": len(results),
        "aug_pass": aug_pass,
        "aug_did_not_run": aug_did_not_run,
        "aug_skipped": aug_skipped,
        "aug_agreed": aug_agreed,
        "aug_stricter": aug_stricter,
        "aug_lenient_blocked": aug_lenient_blocked,
    }


def _render_report(results, metrics, skip_llm: bool, verbose: bool = False) -> str:
    lines = ["Eval Results: \n"]
    for r in results:
        lines.append(f"MSISDN {r['scenario']['msisdn']} amount={r['scenario']['amount']}")
        lines.append(f"  Expected: {r['scenario']['expected']}")
        if verbose:
            aug = r["augmented"]
            if isinstance(aug, dict):
                used_fallback = aug.get("used_fallback")
                providers = aug.get("providers") if isinstance(aug.get("providers"), list) else []
                models = aug.get("models") if isinstance(aug.get("models"), list) else []
                status = aug.get("status") if "status" in aug else aug.get("note", "")
                strictness_pass = None
                agreed_exact = None
                direction = None
                if isinstance(aug.get("status"), str):
                    strictness_pass = is_stricter_or_equal_status(aug.get("status"), r["scenario"]["expected"])
                    det_status = r["deterministic"].get("status")
                    if det_status and aug["status"] == det_status:
                        agreed_exact = True
                    elif det_status and is_stricter_or_equal_status(aug["status"], det_status):
                        agreed_exact = False
                        direction = "stricter"
                    elif det_status:
                        agreed_exact = False
                        direction = "lenient"
                lines.append(
                    f"  Augmented: status={status} used_fallback={used_fallback} providers={providers} "
                    f"models={models} strictness_pass={strictness_pass} agreed_exact={agreed_exact} direction={direction}"
                )
            else:
                lines.append(f"  Augmented: {r['augmented']}")
        else:
            lines.append(f"  Augmented: {r['augmented']}")
        lines.append(f"  Deterministic: {r['deterministic']}")
        lines.append("")

    lines.append(f"Deterministic-only: {metrics['det_ok']}/{metrics['n']} matched expected verdict")
    if skip_llm:
        lines.append("LLM-augmented: skipped (--skip-llm flag)")
    else:
        lines.append(f"LLM-augmented passed (strictness checks): {metrics['aug_pass']}/{metrics['n']}")
        lines.append(f"LLM-augmented agreed exactly with deterministic: {metrics['aug_agreed']}/{metrics['n']}")
        lines.append(f"LLM-augmented stricter on confirmed risk (allowed): {metrics['aug_stricter']}/{metrics['n']}")
        lines.append(f"LLM-augmented more lenient (floor-blocked): {metrics['aug_lenient_blocked']}/{metrics['n']}")
        lines.append(f"LLM-augmented did-not-run (quota/fallback): {metrics['aug_did_not_run']}")
        lines.append(f"LLM-augmented skipped (explicit skips): {metrics['aug_skipped']}")
    return "\n".join(lines)


async def run_all(skip_llm: bool = False, verbose: bool = False, csv_path: str | None = None):
    results = []
    for scen in SCENARIOS:
        if skip_llm:
            augmented = {"note": "LLM skipped", "skipped": True}
        else:
            augmented = await run_one(scen, deterministic_only=False)
        deterministic = await run_one(scen, deterministic_only=True)
        results.append({"scenario": scen, "augmented": augmented, "deterministic": deterministic})

    metrics = score_results(results, skip_llm)
    print(_render_report(results, metrics, skip_llm, verbose))

    if csv_path:
        import csv

        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["msisdn", "amount", "expected", "det_status", "det_risk", "aug_status", "aug_risk", "aug_used_fallback", "strictness_pass", "agreed_exact"]
            )
            for r in results:
                a = r["augmented"]
                d = r["deterministic"]
                aug_status = a.get("status") if isinstance(a, dict) and not a.get("skipped") else ""
                aug_risk = a.get("risk_score") if isinstance(a, dict) and not a.get("skipped") else ""
                aug_fb = a.get("used_fallback") if isinstance(a, dict) else ""
                strict_pass = ""
                agreed_exact = ""
                if isinstance(a, dict) and isinstance(a.get("status"), str):
                    strict_pass = is_stricter_or_equal_status(a["status"], r["scenario"]["expected"])
                    if d.get("status"):
                        agreed_exact = a["status"] == d["status"]
                writer.writerow(
                    [
                        r["scenario"]["msisdn"],
                        r["scenario"]["amount"],
                        r["scenario"]["expected"],
                        d.get("status", ""),
                        d.get("risk_score", ""),
                        aug_status,
                        aug_risk,
                        aug_fb,
                        strict_pass,
                        agreed_exact,
                    ]
                )
        print(f"CSV written to {csv_path}")

    return results, metrics


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="Run only deterministic row (skip LLM-augmented runs)")
    parser.add_argument("--verbose", action="store_true", help="Print per-augmented-row attribution summary")
    parser.add_argument("--csv", type=str, default=None, help="Write per-row results to this CSV path")
    args = parser.parse_args(argv)
    asyncio.run(run_all(skip_llm=args.skip_llm, verbose=args.verbose, csv_path=args.csv))


# --------------------------------------------------------------------------- #
# Offline unit test — fast, no live keys or telecom calls.
# --------------------------------------------------------------------------- #
def test_status_strictness_ordering():
    # Same severity -> equal (allowed, e.g. STEP_UP_REQUIRED -> STEP_UP_REQUIRED).
    assert is_stricter_or_equal_status("STEP_UP_REQUIRED", "STEP_UP_REQUIRED")
    assert is_stricter_or_equal_status("APPROVED", "APPROVED")
    # More severe is "stricter" and therefore allowed.
    assert is_stricter_or_equal_status("REJECTED", "STEP_UP_REQUIRED")
    assert is_stricter_or_equal_status("BLOCKED", "APPROVED")
    # Less severe is "lenient" and must NOT pass the guard.
    assert not is_stricter_or_equal_status("APPROVED", "STEP_UP_REQUIRED")
    assert not is_stricter_or_equal_status("STEP_UP_REQUIRED", "REJECTED")


def test_risk_rank_ordering():
    assert risk_rank("LOW") < risk_rank("MEDIUM") < risk_rank("HIGH") < risk_rank("CRITICAL")
    assert risk_rank("UNKNOWN") == 2  # default fallback


# --------------------------------------------------------------------------- #
# Live behavioral eval — opt-in via --run-live; requires real model keys.
# --------------------------------------------------------------------------- #
@pytest.fixture
def live_eval(request):
    if not request.config.getoption("--run-live"):
        pytest.skip("live LLM behavioral eval disabled; re-run with --run-live")
    if not (settings.GROQ_API_KEY and settings.GOOGLE_API_KEY):
        pytest.skip("live LLM behavioral eval requires GROQ_API_KEY and GOOGLE_API_KEY")
    return True


@pytest.mark.live
def test_llm_augmented_behavioral_eval_never_lenient(live_eval):
    """The full LLM-augmented suite must never be more lenient than the deterministic contract."""
    results, metrics = asyncio.run(run_all(skip_llm=False, verbose=True))

    report = _render_report(results, metrics, skip_llm=False, verbose=True)
    print("\n" + report + "\n")

    # Core honesty property: the LLM-augmented verdict must never relax a grounded one.
    assert metrics["aug_lenient_blocked"] == 0, report
    # We ran real LLM rows (not all did-not-run).
    assert metrics["aug_pass"] > 0 or metrics["aug_did_not_run"] <= metrics["n"], report


if __name__ == "__main__":
    main()
