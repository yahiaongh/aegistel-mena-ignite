# tests/test_nokia_sdk_ladder.py
"""Verifies the Nokia NaC tool ladder for every CAMARA tool:

    Tier 1 -- Nokia NaC SDK succeeds           -> source "Nokia NaC SDK"
    Tier 2 -- SDK fails, CAMARA REST succeeds  -> source "Nokia NaC REST API"
    Tier 3 -- SDK and REST both fail           -> sandbox fallback source

This pins the exact ordering the live backend depends on: the SDK is always
attempted first (even on the first call, when the client has not been warmed),
then REST, then sandbox. Regression guard for the dead-`nac_client` guard bug
where the SDK tier was unreachable because the module-global started as None and
nothing ever initialized it at tool-call time.
"""
import types
from datetime import datetime, timedelta, timezone

import pytest

from app.agents import tools as tool_module
from app.agents.tools import (
    check_device_reachability,
    check_device_swap,
    check_number_recycling,
    check_roaming_status,
    check_sim_swap,
    create_qod_session,
    get_congestion_insights,
    verify_location,
    verify_number,
)

ALL_TOOLS = [
    "check_sim_swap",
    "check_device_swap",
    "verify_location",
    "check_roaming_status",
    "check_device_reachability",
    "create_qod_session",
    "verify_number",
    "get_congestion_insights",
    "check_number_recycling",
]


def _call(tool_name: str, msisdn: str = "+99999991001") -> str:
    if tool_name == "check_sim_swap":
        return check_sim_swap(msisdn)
    if tool_name == "check_device_swap":
        return check_device_swap(msisdn)
    if tool_name == "verify_location":
        return verify_location(msisdn, 25.2, 55.2)
    if tool_name == "check_roaming_status":
        return check_roaming_status(msisdn)
    if tool_name == "check_device_reachability":
        return check_device_reachability(msisdn)
    if tool_name == "create_qod_session":
        return create_qod_session(msisdn)
    if tool_name == "verify_number":
        return verify_number(msisdn)
    if tool_name == "get_congestion_insights":
        return get_congestion_insights(msisdn)
    if tool_name == "check_number_recycling":
        return check_number_recycling(msisdn)
    raise AssertionError(f"unknown tool {tool_name}")


class _WorkingClient:
    """SDK-shaped stub that answers every CAMARA signal successfully."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.sim_swap = types.SimpleNamespace(
            check=lambda phone_number=None, max_age=None: types.SimpleNamespace(
                swapped=False
            ),
            retrieve_date=lambda phone_number=None: types.SimpleNamespace(
                latest_sim_change=types.SimpleNamespace(
                    isoformat=lambda: "2026-01-01T00:00:00+00:00"
                )
            ),
        )
        self.device_swap = types.SimpleNamespace(
            check=lambda phone_number=None, max_age=None: types.SimpleNamespace(
                swapped=False
            )
        )
        self.location = types.SimpleNamespace(
            verify_v1=lambda device=None, area=None, max_age=None: types.SimpleNamespace(
                verification_result="TRUE"
            )
        )
        self.device_status = types.SimpleNamespace(
            retrieve_roaming_status=lambda device=None: types.SimpleNamespace(
                roaming=False, country_code=None, country_name=[]
            ),
            retrieve_reachability_status=lambda device=None: types.SimpleNamespace(
                reachable=True, connectivity=["DATA", "SMS"]
            ),
        )
        self.qod = types.SimpleNamespace(
            create_session_v1=lambda application_server=None, qos_profile=None, device=None, duration=None: types.SimpleNamespace(
                session_id="sdk-qod-session", qos_status="REQUESTED"
            )
        )
        self.number_verification = types.SimpleNamespace(
            verify_v2=lambda request=None, **kwargs: types.SimpleNamespace(
                device_phone_number_verified=True
            )
        )
        self.congestion_insights = types.SimpleNamespace(
            query=lambda device=None, start=None, end=None: [
                types.SimpleNamespace(
                    time_interval_start=now - timedelta(minutes=30),
                    time_interval_stop=now,
                    congestion_level="Low",
                    confidence_level=95,
                )
            ]
        )
        self.number_recycling = types.SimpleNamespace(
            check=lambda phone_number=None, specified_date=None: types.SimpleNamespace(
                phoneNumberRecycled=False
            )
        )

    def __getattr__(self, name):
        raise AttributeError(f"_WorkingClient has no module {name!r}")


def _raise(*_args, **_kwargs):
    raise RuntimeError("simulated SDK failure")


def _raising_client() -> types.SimpleNamespace:
    modules = {}
    for name in (
        "sim_swap",
        "device_swap",
        "location",
        "device_status",
        "qod",
        "number_verification",
        "congestion_insights",
        "number_recycling",
    ):
        modules[name] = types.SimpleNamespace(
            check=_raise,
            retrieve_date=_raise,
            verify_v1=_raise,
            retrieve_roaming_status=_raise,
            retrieve_reachability_status=_raise,
            verify_v2=_raise,
            create_session_v1=_raise,
            query=_raise,
        )
    return types.SimpleNamespace(**modules)


class _RESTOk:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


_REST_PAYLOAD = {
    "swapped": True,
    "deviceSwapped": True,
    "verificationResult": "TRUE",
    "roaming": True,
    "countryCode": None,
    "countryName": [],
    "reachable": True,
    "connectivity": ["DATA"],
    "sessionId": "rest-qod-session",
    "qosStatus": "REQUESTED",
    "devicePhoneNumberVerified": True,
    "congestionLevels": [{"congestionLevel": "High"}],
    "phoneNumberRecycled": True,
}


@pytest.fixture
def use_working_sdk(monkeypatch):
    monkeypatch.setattr(tool_module, "nac_client", _WorkingClient())


@pytest.mark.parametrize("tool_name", ALL_TOOLS)
def test_tier1_sdk_success(monkeypatch, use_working_sdk, tool_name):
    result = _call(tool_name)
    body = __import__("json").loads(result)
    assert body["source"] == "Nokia NaC SDK", f"{tool_name} did not use the SDK: {body}"


@pytest.mark.parametrize("tool_name", ALL_TOOLS)
def test_tier2_rest_fallback(monkeypatch, use_working_sdk, tool_name):
    monkeypatch.setattr(tool_module, "nac_client", _raising_client())
    calls = {"count": 0}

    def _post(*_args, **_kwargs):
        calls["count"] += 1
        return _RESTOk(dict(_REST_PAYLOAD))

    monkeypatch.setattr(tool_module.requests, "post", _post)
    result = _call(tool_name)
    body = __import__("json").loads(result)
    assert body["source"] == "Nokia NaC REST API", f"{tool_name} did not fall to REST: {body}"
    assert calls["count"] >= 1, f"{tool_name} never attempted the REST tier"


@pytest.mark.parametrize("tool_name", ALL_TOOLS)
def test_tier3_sandbox_fallback(monkeypatch, use_working_sdk, tool_name):
    monkeypatch.setattr(tool_module, "nac_client", _raising_client())

    def _post(*_args, **_kwargs):
        raise OSError("simulated REST failure")

    monkeypatch.setattr(tool_module.requests, "post", _post)
    result = _call(tool_name)
    body = __import__("json").loads(result)
    source = body["source"]
    assert "sandbox" in source.lower(), f"{tool_name} did not reach sandbox tier: {body}"
    assert tool_module._get_nac_client() is not None, "lazy SDK client should be cached"


def test_lazy_init_is_attempted_even_though_global_starts_none(monkeypatch):
    """Regression: the SDK tier was dead code because `nac_client` starts None
    and was never initialized at tool-call time. With the stub SDK in place the
    tool must now take the SDK tier on the very first invocation."""
    monkeypatch.setattr(tool_module, "nac_client", None)
    monkeypatch.setattr(
        tool_module,
        "_get_nac_client",
        lambda: _WorkingClient(),
        raising=False,
    )
    result = check_sim_swap("+99999991001")
    body = __import__("json").loads(result)
    assert body["source"] == "Nokia NaC SDK"