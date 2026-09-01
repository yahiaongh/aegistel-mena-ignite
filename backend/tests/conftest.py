import os
import tempfile
import types
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ROOT_ENV_FILE, override=False)


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run opt-in live behavioral eval tests that need real model/telecom access.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: opt-in behavioral eval requiring live model + telecom access (run with --run-live)")

# CRITICAL: the test suite must never touch the live demo memory file
# (backend/data/local_memory.jsonl). Earlier, tests called clear_all_memory()
# against the real store and wiped the operator's accumulated audit history.
# Route every test-run memory write to a scratch file under the OS temp dir.
_TEST_MEMORY_PATH = Path(tempfile.mkdtemp(prefix="aegistel-test-memory-")) / "local_memory.jsonl"
os.environ["AEGISTEL_MEMORY_PATH"] = str(_TEST_MEMORY_PATH)


@pytest.fixture(autouse=True)
def reset_provider_cooldown():
    from app.agents import crew_specialists

    crew_specialists._PROVIDER_COOLDOWN.clear()
    yield
    crew_specialists._PROVIDER_COOLDOWN.clear()


@pytest.fixture(autouse=True)
def patch_nokia_sdk(request, monkeypatch):
    # Live behavioral eval must exercise the REAL telecom tools/SDK, not the stub.
    if request.node.get_closest_marker("live"):
        yield
        return

    from app.agents import tools as tool_module

    class _FakeNacClient:
        def __init__(self) -> None:
            self.sim_swap = types.SimpleNamespace(
                check=self._check_swap,
                retrieve_date=self._retrieve_date,
            )
            self.location = types.SimpleNamespace(verify_v1=self._verify_location)
            self.device_status = types.SimpleNamespace(
                retrieve_roaming_status=self._retrieve_roaming_status,
                retrieve_reachability_status=self._retrieve_reachability_status,
            )
            self.qod = types.SimpleNamespace(create_session_v1=self._create_qod_session)

        def _check_swap(self, phone_number: str, max_age: int):
            return types.SimpleNamespace(swapped=(phone_number == "+99999991000"))

        def _retrieve_date(self, phone_number: str):
            return types.SimpleNamespace(latest_sim_change=types.SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"))

        def _verify_location(self, device: dict, area: dict, max_age: int):
            phone_number = device.get("phone_number", "")
            if phone_number == "+99999991000":
                return types.SimpleNamespace(verification_result="FALSE")
            return types.SimpleNamespace(verification_result="TRUE")

        def _retrieve_roaming_status(self, device: dict):
            return types.SimpleNamespace(roaming=False, country_code=None, country_name=[])

        def _retrieve_reachability_status(self, device: dict):
            return types.SimpleNamespace(reachable=True, connectivity=["DATA", "SMS"])

        def _create_qod_session(self, application_server: dict, qos_profile: str, device: dict, duration: int):
            return types.SimpleNamespace(session_id="sdk-qod-session", qos_status="REQUESTED")

    fake_client = _FakeNacClient()
    monkeypatch.setattr(tool_module, "nac_client", fake_client)

    def _raise_fallback(*args, **kwargs):
        raise AssertionError("REST fallback should not be used in the SDK-backed test harness")

    monkeypatch.setattr(tool_module.requests, "post", _raise_fallback)
    yield
