import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_module


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setattr(main_module.settings, "AEGISTEL_ADMIN_KEY", "s3cret-admin-token")
    with TestClient(main_module.app) as client:
        yield client


def _submit(client, ratings=None, **extra):
    body = {"ratings": ratings if ratings is not None else {"ui": 5, "verdict": 4}}
    body.update(extra)
    return client.post("/api/feedback", json=body)


def test_feedback_public_submit_and_admin_readback(admin_client):
    response = _submit(admin_client, comment="Loved the blocked transfer", role="judge", mood="😍")
    assert response.status_code == 201
    payload = response.json()
    assert payload["ok"] is True
    feedback_id = payload["id"]

    read = admin_client.get("/api/feedback", headers={"X-Admin-Token": "s3cret-admin-token"})
    assert read.status_code == 200
    data = read.json()
    assert data["summary"]["count"] >= 1
    assert data["summary"]["per_feature"]["ui"]["avg"] == 5.0
    assert feedback_id in [r["id"] for r in data["latest"]]


def test_feedback_requires_at_least_one_rating(admin_client):
    response = _submit(admin_client, ratings={})
    assert response.status_code == 422
    assert "rating" in response.json()["detail"].lower()


def test_feedback_sanitizes_unknown_features_and_out_of_range(admin_client):
    response = _submit(
        admin_client,
        ratings={"ui": 5, "bogus": 9, "verdict": 11, "performance": 2},
    )
    assert response.status_code == 201
    saved = response.json()["ratings"]
    assert set(saved.keys()) == {"ui", "performance"}
    assert saved["ui"] == 5
    assert saved["performance"] == 2


def test_feedback_rejects_invalid_payload(admin_client):
    assert _submit(admin_client, ratings="not-a-dict").status_code == 422
    assert admin_client.post("/api/feedback", data="not json").status_code == 400


def test_feedback_admin_requires_valid_token(admin_client):
    assert admin_client.get("/api/feedback").status_code == 401
    assert admin_client.get("/api/feedback?token=wrong").status_code == 401
    assert admin_client.get("/api/feedback", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_feedback_admin_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(main_module.settings, "AEGISTEL_ADMIN_KEY", "")
    with TestClient(main_module.app) as client:
        response = client.get("/api/feedback")
    assert response.status_code == 503


def test_feedback_summary_averages_and_tallies(admin_client):
    loose = _submit(admin_client, ratings={"drill": 4}, role="investor", mood="😊")
    strict = _submit(admin_client, ratings={"drill": 2}, role="judge", mood="😍")
    assert loose.status_code == 201 and strict.status_code == 201

    data = admin_client.get("/api/feedback", headers={"X-Admin-Token": "s3cret-admin-token"}).json()
    drill = data["summary"]["per_feature"]["drill"]
    assert drill["count"] >= 2
    assert drill["avg"] == pytest.approx(3.0)
    assert drill["distribution"]["4"] >= 1 and drill["distribution"]["2"] >= 1
    assert data["summary"]["moods"].get("😊", 0) >= 1
    assert data["summary"]["roles"].get("investor", 0) >= 1


def test_feedback_mood_and_role_validation(admin_client):
    response = _submit(admin_client, ratings={"ui": 3}, mood="👽", role="ROYALTY")
    assert response.status_code == 201
    saved = admin_client.get("/api/feedback", headers={"X-Admin-Token": "s3cret-admin-token"}).json()["latest"][0]
    assert saved["mood"] is None
    assert saved["role"] == "other"


def test_feedback_context_and_meta_captured(admin_client):
    response = _submit(
        admin_client,
        ratings={"voice": 4},
        context={"msisdn": "+99999991001", "last_status": "BLOCKED", "nested": {"deep": [1, 2, 3]}},
    )
    assert response.status_code == 201
    record_id = response.json()["id"]
    data = admin_client.get("/api/feedback", headers={"X-Admin-Token": "s3cret-admin-token"}).json()
    record = next(r for r in data["latest"] if r["id"] == record_id)
    assert record["context"]["msisdn"] == "+99999991001"
    assert record["context"]["last_status"] == "BLOCKED"
    assert "nested" not in record["context"]
    assert "meta" in record and "client_ip" in record["meta"]