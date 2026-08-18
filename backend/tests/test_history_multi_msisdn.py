"""Aggressive multi-MSISDN audit-history tests.

History is the operator's trusted record of every audit. These tests hammer the
feature across many MSISDNs: accumulation, ordering, limits, MSISDN
normalization, disk persistence, per-subscriber isolation, and the full
execute_audit -> history API path.
"""
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents import memory_agent
from app.agents.graph_orchestrator import execute_audit
from app.agents.memory_agent import memory_engine
from app.main import app
from app.schemas.telemetry import AuditRequest, LocationInput


def _record(msisdn: str, status: str, amount: float, risk: str = "LOW") -> None:
    memory_engine.record_incident(
        msisdn,
        f"WIRE_TRANSFER risk={risk} status={status}",
        {"amount": amount, "risk_score": risk, "status": status, "roaming_status": "DOMESTIC"},
    )


def _history(msisdn: str, limit: int = 8) -> dict:
    response = TestClient(app).get(f"/api/v1/history/{msisdn}?limit={limit}")
    assert response.status_code == 200
    return response.json()


def test_many_audits_accumulate_for_many_msisdns() -> None:
    memory_engine.clear_all_memory()
    msisdns = ["+99999991000", "+99999991001", "+99999991002", "+99999991003", "+99999991004"]
    for i, msisdn in enumerate(msisdns):
        for j in range(5):
            _record(msisdn, "APPROVED", 1000 * (i + 1) + j)

    for msisdn in msisdns:
        payload = _history(msisdn, limit=20)
        assert payload["count"] == 5, f"{msisdn}: expected 5 records, got {payload['count']}"
        assert [item["amount"] for item in payload["incidents"]] == [
            1000 * (msisdns.index(msisdn) + 1) + j for j in range(5)
        ]


def test_history_slice_returns_most_recent_records() -> None:
    memory_engine.clear_all_memory()
    for j in range(12):
        _record("+99999991000", "APPROVED", 100.0 + j)

    full = _history("+99999991000", limit=20)
    assert full["count"] == 12
    assert full["incidents"][0]["amount"] == 100.0
    assert full["incidents"][-1]["amount"] == 111.0

    sliced = _history("+99999991000", limit=5)
    assert sliced["count"] == 5
    # The 5 most RECENT audits (append-ordered store, newest at the end).
    assert [item["amount"] for item in sliced["incidents"]] == [107.0, 108.0, 109.0, 110.0, 111.0]


def test_history_survives_engine_reload_from_disk() -> None:
    memory_engine.clear_all_memory()
    for j in range(4):
        _record("+99999991001", "APPROVED", 500.0 + j)

    reloaded = memory_agent.NetworkMemoryEngine()
    incidents = reloaded.list_all_incidents("+99999991001")
    assert len(incidents) == 4


def test_msisdn_normalization_forms_share_history() -> None:
    memory_engine.clear_all_memory()
    _record("+1001", "APPROVED", 1.0)
    _record("1001", "APPROVED", 2.0)
    _record("  1001  ", "APPROVED", 3.0)

    for form in ("+1001", "1001", "%2B1001"):
        payload = _history(form)
        assert payload["count"] == 3, f"form {form!r} -> {payload['count']}"
        assert [item["amount"] for item in payload["incidents"]] == [1.0, 2.0, 3.0]


def test_history_is_isolated_between_msisdns() -> None:
    memory_engine.clear_all_memory()
    _record("+99999991000", "BLOCKED", 9000.0, risk="CRITICAL")
    _record("+99999991001", "APPROVED", 5.0)

    a = _history("+99999991000")
    b = _history("+99999991001")
    assert a["count"] == 1 and a["incidents"][0]["status"] == "BLOCKED"
    assert b["count"] == 1 and b["incidents"][0]["status"] == "APPROVED"


def test_execute_audit_records_across_many_msisdns_end_to_end() -> None:
    memory_engine.clear_all_memory()
    msisdns = ["+99999991000", "+99999991001", "+99999991002", "+99999991003", "+99999991004"]
    for i, msisdn in enumerate(msisdns):
        result = asyncio.run(
            execute_audit(
                AuditRequest(
                    msisdn=msisdn,
                    amount=float(2000 * (i + 1)),
                    transaction_type="WIRE_TRANSFER",
                    current_location=LocationInput(latitude=24.0, longitude=46.0),
                    request_qod_slice=False,
                    metadata={"_force_deterministic": True},
                )
            )
        )
        assert result.status in {"APPROVED", "STEP_UP_REQUIRED", "REJECTED", "BLOCKED"}

    for msisdn in msisdns:
        payload = _history(msisdn)
        assert payload["count"] == 1, f"{msisdn}: {payload['count']}"
        assert payload["incidents"][0]["amount"] is not None
        assert payload["incidents"][0]["timestamp"] is not None


def test_repeated_full_audits_accumulate_in_history() -> None:
    memory_engine.clear_all_memory()
    for _ in range(4):
        asyncio.run(
            execute_audit(
                AuditRequest(
                    msisdn="+99999991003",
                    amount=2500.0,
                    transaction_type="WIRE_TRANSFER",
                    current_location=LocationInput(latitude=24.0, longitude=46.0),
                    request_qod_slice=False,
                    metadata={"_force_deterministic": True},
                )
            )
        )

    payload = _history("+99999991003")
    assert payload["count"] == 4
    timestamps = [item["timestamp"] for item in payload["incidents"]]
    assert len(set(timestamps)) == 4  # every audit is a distinct record


def test_tests_never_touch_the_production_memory_file() -> None:
    production = Path(__file__).resolve().parents[2] / "data" / "local_memory.jsonl"
    assert str(memory_agent.LOCAL_STORE_PATH) != str(production)
    assert "aegistel-test-memory" in str(memory_agent.LOCAL_STORE_PATH)