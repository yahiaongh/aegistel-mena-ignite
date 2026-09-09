# backend/scripts/benchmark_decision.py
"""Measure AegisTel decision-path latency with three honest, explicit modes.

Usage (from backend/):
    ../venv/bin/python -m scripts.benchmark_decision                  # offline (default)
    ../venv/bin/python -m scripts.benchmark_decision --nac-sandbox   # real NAC attempts → fallback
    ../venv/bin/python -m scripts.benchmark_decision --live          # real LLM + live NAC (inline)

Modes:
  --offline      Genuinely network-free. The Nokia NaC SDK client is replaced
                 in-process with a stub that reproduces the documented sandbox
                 semantics, direct REST fallback is blocked, LLM keys are
                 blanked (deterministic crew fallback), and memory is
                 redirected to a scratch file. No bytes cross the wire.
  --nac-sandbox  The real Nokia SDK/REST calls run (401/404/… typical on
                 unprovisioned keys) and every tool lands on its documented
                 sandbox fallback. LLM stays disabled. This is a live-NAC
                 sandbox measurement, NOT a deterministic-offline one.
  --live         The full inline path: real NAC attempts AND the CrewAI LLM
                 chain, awaited before the verdict returns (enrichment is not
                 yet two-stage).

Every mode prints p50/p95/max wall-clock and a pass/fail against the
documented target: **sub-5-second deterministic-only decisioning**.
"""
import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
import types
from pathlib import Path

# --- Environment guard: must run BEFORE any app module is imported. ---------
# LiteLLM must never dial out for its remote model-price map, remote memory
# clients must never auto-build, and the benchmark must never write into the
# live demo memory store.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "ERROR")
os.environ.setdefault("AEGISTEL_LIVE_MEMORY", "0")
os.environ["AEGISTEL_MEMORY_PATH"] = str(
    Path(tempfile.mkdtemp(prefix="aegistel-bench-memory-")) / "local_memory.jsonl"
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.telemetry import AuditRequest  # noqa: E402
from app.agents.graph_orchestrator import execute_audit  # noqa: E402
from app.agents import tools as tool_module  # noqa: E402
from app.agents import crew_specialists  # noqa: E402
from app.core.config import settings  # noqa: E402

TARGET_SECONDS = 5.0

SCENARIOS = [
    {
        "label": "clean-p2p-sim",
        "msisdn": "+99999991001",
        "amount": 100.0,
        "transaction_type": "P2P_TRANSFER",
        "location": (24.7136, 46.6753),
    },
    {
        "label": "ato-wire-sim",
        "msisdn": "+99999991000",
        "amount": 120000.0,
        "transaction_type": "WIRE_TRANSFER",
        "location": (25.2048, 55.2708),
    },
    {
        "label": "random-e164",
        "msisdn": "+966500100000",
        "amount": 4600.0,
        "transaction_type": "MOBILE_PAYMENT",
        "location": (24.7136, 46.6753),
    },
]

MODE_LABELS = {
    "offline": "OFFLINE deterministic (NAC client stubbed in-process, REST blocked, LLM off, zero network)",
    "nac-sandbox": "NAC SANDBOX (live Nokia SDK/REST attempts -> documented fallback; LLM disabled)",
    "live": "LIVE inline (real Nokia NAC attempts + CrewAI LLM chain, awaited before verdict)",
}


def _blank_llm_keys() -> None:
    """Force the deterministic crew fallback exactly like the offline regression
    suite does: with no provider credential available, run_specialist_crew
    short-circuits to the deterministic engine without any model call."""
    for key in ("GROQ_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "CEREBRAS_API_KEY"):
        setattr(settings, key, "")
    crew_specialists._PROVIDER_COOLDOWN.clear()


def _offline_nac_stub() -> None:
    """Replace the Nokia NaC SDK client with an in-process stub that reproduces
    the documented sandbox semantics, and block the REST fallback so the run is
    provably network-free. Mirrors the offline test-suite harness."""

    def _swap_flag(phone_number: str) -> bool:
        return phone_number == "+99999991000"

    class _FakeNacClient:
        def __init__(self) -> None:
            self.sim_swap = types.SimpleNamespace(
                check=lambda phone_number, max_age: types.SimpleNamespace(
                    swapped=_swap_flag(phone_number)
                ),
                retrieve_date=lambda phone_number: types.SimpleNamespace(
                    latest_sim_change=types.SimpleNamespace(
                        isoformat=lambda: "2026-01-01T00:00:00+00:00"
                    )
                ),
            )
            self.location = types.SimpleNamespace(
                verify_v1=lambda device, area, max_age: types.SimpleNamespace(
                    verification_result="FALSE"
                    if device.get("phone_number") == "+99999991000"
                    else "TRUE"
                )
            )
            self.device_status = types.SimpleNamespace(
                retrieve_roaming_status=lambda device: types.SimpleNamespace(
                    roaming=False, country_code=None, country_name=[]
                ),
                retrieve_reachability_status=lambda device: types.SimpleNamespace(
                    reachable=True, connectivity=["DATA", "SMS"]
                ),
            )
            self.qod = types.SimpleNamespace(
                create_session_v1=lambda application_server, qos_profile, device, duration: types.SimpleNamespace(
                    session_id="stub-qod-session", qos_status="REQUESTED"
                )
            )
            self.number_verification = types.SimpleNamespace(
                verify_v2=lambda request: types.SimpleNamespace(
                    device_phone_number_verified=_number_verification_sandbox(
                        request.get("phone_number")
                    )
                )
            )
            self.congestion_insights = types.SimpleNamespace(
                query=lambda device, start, end: _congestion_sandbox_series(
                    device.get("phone_number")
                )
            )

    def _number_verification_sandbox(msisdn: str):
        if msisdn == "+99999991000":
            return False
        if msisdn == "+99999991001":
            return True
        return None  # "unknown, not verified" — the honest unknown on non-demo numbers

    def _congestion_sandbox_series(msisdn: str):
        from datetime import datetime, timedelta, timezone

        level = {"+99999991000": "High", "+99999991001": "Low", "+99999991002": "Medium"}.get(msisdn, "Low")
        now = datetime.now(timezone.utc)
        return [
            types.SimpleNamespace(
                time_interval_start=now - timedelta(minutes=30),
                time_interval_stop=now,
                congestion_level=level,
                confidence_level=95,
            )
        ]

    tool_module.nac_client = _FakeNacClient()

    def _no_egress(*args, **kwargs):
        raise AssertionError("offline benchmark: direct REST fallback must not egress")

    tool_module.requests.post = _no_egress


def _request(scenario: dict) -> AuditRequest:
    return AuditRequest(
        msisdn=scenario["msisdn"],
        amount=scenario["amount"],
        transaction_type=scenario["transaction_type"],
        current_location={"latitude": scenario["location"][0], "longitude": scenario["location"][1]},
    )


def _report(label: str, samples: list[float], mode: str) -> None:
    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1] if len(samples) > 1 else samples[0]
    mx = max(samples)
    ok = p50 <= TARGET_SECONDS
    print(f"[{label}] runs={len(samples)} p50={p50:.2f}s p95={p95:.2f}s max={mx:.2f}s "
          f"{'OK' if ok else 'OVER'} target={TARGET_SECONDS}s — {MODE_LABELS[mode]}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="network-free stub (default)")
    parser.add_argument("--nac-sandbox", action="store_true", help="live NAC attempts -> sandbox fallback; no LLM")
    parser.add_argument("--live", action="store_true", help="full inline LLM + live NAC path")
    parser.add_argument("--runs", type=int, default=5, help="audits per scenario")
    args = parser.parse_args()

    modes = [name for name, flag in (("offline", args.offline), ("nac-sandbox", args.nac_sandbox), ("live", args.live)) if flag]
    mode = modes[-1] if modes else "offline"
    if len(modes) > 1:
        parser.error("choose exactly one mode: --offline, --nac-sandbox, or --live")
    if mode == "live":
        print("WARNING: --live makes real LLM + NAC calls and is not repeatable/offline-safe.")
    else:
        _blank_llm_keys()
    if mode == "offline":
        _offline_nac_stub()

    print(f"Benchmarking AegisTel decision path ({MODE_LABELS[mode]}; {args.runs} runs/scenario)")
    print(f"Target: deterministic-only verdict < {TARGET_SECONDS}s. "
          "The live LLM-enhanced path is currently inline (post-decision async enrichment is a roadmap item).")
    print()

    for scenario in SCENARIOS:
        samples: list[float] = []
        statuses: set[str] = set()
        for _ in range(args.runs):
            started = time.monotonic()
            resp = await execute_audit(_request(scenario), tenant_id="benchmark")
            samples.append(time.monotonic() - started)
            statuses.add(resp.status)
        _report(scenario["label"], samples, mode)
        print(f"    verdicts: {sorted(statuses)}")

    print()
    if mode == "offline":
        print("This offline run is provably network-free: NAC client stubbed in-process,")
        print("REST fallback blocked, LLM keys blanked, memory redirected to scratch.")
    print("The LLM-enhanced path remains inline: a --live run waits for the CrewAI chain")
    print("and can float toward LLM latency (~69s worst observed with live keys + memory).")
    print("Making enrichment a true two-stage step (verdict first, LLM polish async after)")
    print("is a declared roadmap item — today the sub-5s target covers the deterministic-only path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))