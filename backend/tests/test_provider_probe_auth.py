"""Provider-probe endpoint auth tests.

/diagnostics/provider_probe fires authenticated requests at every configured
paid/free-tier LLM provider and reflects their status. It must be operator-only:
never reachable unauthenticated, and it must fail closed (503) when the
deployment has not configured an admin key.
"""

import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

client = TestClient(app)


def _stub_requests_get(monkeypatch) -> None:
    """Replace requests.get so the authorized path does not burn real provider
    quota during tests."""

    def _get(url: str, headers: dict | None = None, timeout: float | None = None):
        return SimpleNamespace(status_code=200, text="{}")

    monkeypatch.setattr("requests.get", _get)


def test_provider_probe_requires_operator_auth(monkeypatch) -> None:
    _stub_requests_get(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_ADMIN_KEY", "s3cret-admin-token")
    response = client.get("/api/diagnostics/provider_probe")
    assert response.status_code == 401


def test_provider_probe_accepts_operator_token(monkeypatch) -> None:
    _stub_requests_get(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_ADMIN_KEY", "s3cret-admin-token")
    response = client.get(
        "/api/diagnostics/provider_probe",
        headers={"Authorization": "Bearer s3cret-admin-token"},
    )
    assert response.status_code == 200
    assert "probes" in response.json()


def test_provider_probe_fails_closed_without_admin_key(monkeypatch) -> None:
    _stub_requests_get(monkeypatch)
    monkeypatch.setattr(main_module.settings, "AEGISTEL_ADMIN_KEY", "")
    response = client.get(
        "/api/diagnostics/provider_probe",
        headers={"Authorization": "Bearer whatever"},
    )
    assert response.status_code == 503