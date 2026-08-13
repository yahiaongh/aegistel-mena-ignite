import argparse
import asyncio
from collections import Counter

from app.agents.graph_orchestrator import execute_audit
from app.schemas.telemetry import AuditRequest, LocationInput
from app.agents.memory_agent import memory_engine
from app.core.config import settings


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


def is_stricter_or_equal_status(actual: str, expected: str) -> bool:
    order = ["APPROVED", "STEP_UP_REQUIRED", "MANUAL_REVIEW", "REJECTED", "BLOCKED"]
    try:
        return order.index(actual) >= order.index(expected)
    except ValueError:
        return False


async def run_all(skip_llm: bool = False, verbose: bool = False, csv_path: str | None = None):
    results = []
    for scen in SCENARIOS:
        if skip_llm:
            augmented = {"note": "LLM skipped", "skipped": True}
        else:
            augmented = await run_one(scen, deterministic_only=False)
        deterministic = await run_one(scen, deterministic_only=True)
        results.append({"scenario": scen, "augmented": augmented, "deterministic": deterministic})

    # Scoring
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
            # LLM did not run (fallback) — report separately
            aug_did_not_run += 1
        elif isinstance(a, dict) and a.get("status"):
            # If the LLM ran, it must be at least as strict and not reduce risk
            status_ok = is_stricter_or_equal_status(a["status"], expected)
            # risk comparison
            severity = {"LOW":1, "MEDIUM":2, "HIGH":3, "CRITICAL":4}
            det_risk_rank = severity.get(r["deterministic"].get("risk_score"), 2)
            aug_risk_rank = severity.get(a.get("risk_score"), det_risk_rank)
            risk_ok = aug_risk_rank >= det_risk_rank
            if status_ok and risk_ok:
                aug_pass += 1
            # Exact-agreement accounting (coherence, not just strictness)
            if det_status and a.get("status") == det_status:
                aug_agreed += 1
            elif det_status and is_stricter_or_equal_status(a["status"], det_status):
                aug_stricter += 1
            elif det_status:
                aug_lenient_blocked += 1

    print("Eval Results: \n")
    for r in results:
        print(f"MSISDN {r['scenario']['msisdn']} amount={r['scenario']['amount']}")
        print('  Expected:', r['scenario']['expected'])
        if verbose:
            aug = r['augmented']
            if isinstance(aug, dict):
                used_fallback = aug.get('used_fallback')
                providers = aug.get('providers') if isinstance(aug.get('providers'), list) else []
                models = aug.get('models') if isinstance(aug.get('models'), list) else []
                status = aug.get('status') if 'status' in aug else aug.get('note', '')
                strictness_pass = None
                agreed_exact = None
                direction = None
                if isinstance(aug.get('status'), str):
                    strictness_pass = is_stricter_or_equal_status(aug.get('status'), r['scenario']['expected'])
                    det_status = r["deterministic"].get("status")
                    if det_status and aug["status"] == det_status:
                        agreed_exact = True
                    elif det_status and is_stricter_or_equal_status(aug["status"], det_status):
                        agreed_exact = False
                        direction = "stricter"
                    elif det_status:
                        agreed_exact = False
                        direction = "lenient"
                print(f"  Augmented: status={status} used_fallback={used_fallback} providers={providers} models={models} strictness_pass={strictness_pass} agreed_exact={agreed_exact} direction={direction}")
            else:
                print(f"  Augmented: {r['augmented']}")
        else:
            print('  Augmented:', r['augmented'])
        print('  Deterministic:', r['deterministic'])
        print('')

    print(f"Deterministic-only: {det_ok}/{len(results)} matched expected verdict")
    if skip_llm:
        print("LLM-augmented: skipped (--skip-llm flag)")
    else:
        print(f"LLM-augmented passed (strictness checks): {aug_pass}/{len(results)}")
        print(f"LLM-augmented agreed exactly with deterministic: {aug_agreed}/{len(results)}")
        print(f"LLM-augmented stricter on confirmed risk (allowed): {aug_stricter}/{len(results)}")
        print(f"LLM-augmented more lenient (floor-blocked): {aug_lenient_blocked}/{len(results)}")
        print(f"LLM-augmented did-not-run (quota/fallback): {aug_did_not_run}")
        print(f"LLM-augmented skipped (explicit skips): {aug_skipped}")

    if csv_path:
        import csv

        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["msisdn", "amount", "expected", "det_status", "det_risk", "aug_status", "aug_risk", "aug_used_fallback", "strictness_pass", "agreed_exact"])
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
                writer.writerow([
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
                ])
        print(f"CSV written to {csv_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="Run only deterministic row (skip LLM-augmented runs)")
    parser.add_argument("--verbose", action="store_true", help="Print per-augmented-row attribution summary")
    parser.add_argument("--csv", type=str, default=None, help="Write per-row results to this CSV path")
    args = parser.parse_args()
    asyncio.run(run_all(skip_llm=args.skip_llm, verbose=args.verbose, csv_path=args.csv))
