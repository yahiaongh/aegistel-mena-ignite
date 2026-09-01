import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_module


@pytest.fixture
def client(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice, rate, pitch):
            self.text = text
            self.voice = voice
            self.rate = rate
            self.pitch = pitch

        async def stream(self):
            yield {"type": "audio", "data": b"audio-bytes"}

    monkeypatch.setattr(main_module.settings, "DEEPGRAM_API_KEY", "fake-key")
    monkeypatch.setattr(main_module, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))

    requests_module = types.ModuleType("requests")

    class BrokenPost:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("Deepgram exploded")

    requests_module.post = BrokenPost()
    monkeypatch.setitem(sys.modules, "requests", requests_module)

    with TestClient(main_module.app) as test_client:
        yield test_client


def test_tts_uses_deepgram_and_never_silently_degrades_when_configured(client):
    # A configured-but-failing Deepgram key must be surfaced, NOT silently
    # swapped for edge_tts — the demo voice must be exactly what was configured.
    response = client.post(
        "/api/audio/tts",
        data={"text": "hello", "voice": "ar-EG-ShakirNeural"},
    )

    assert response.status_code == 503
    assert "Deepgram" in response.json()["detail"]


def test_tts_returns_deepgram_audio_on_success(monkeypatch):
    requests_module = types.ModuleType("requests")

    class WorkingPost:
        def __call__(self, *args, **kwargs):
            return types.SimpleNamespace(raise_for_status=lambda: None, status_code=200, content=b"deepgram-audio")

    requests_module.post = WorkingPost()
    monkeypatch.setitem(sys.modules, "requests", requests_module)
    monkeypatch.setattr(main_module.settings, "DEEPGRAM_API_KEY", "valid-key")

    with TestClient(main_module.app) as test_client:
        response = test_client.post(
            "/api/audio/tts",
            data={"text": "hi", "voice": "ar-EG-ShakirNeural"},
        )

    assert response.status_code == 200
    assert response.headers["x-tts-source"] == "deepgram"
    assert response.content == b"deepgram-audio"


def test_tts_normalizes_phone_number_before_dispatch(monkeypatch):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate, pitch):
            captured["text"] = text
            self.text = text
            self.voice = voice
            self.rate = rate
            self.pitch = pitch

        async def stream(self):
            yield {"type": "audio", "data": b"audio-bytes"}

    monkeypatch.setattr(main_module.settings, "DEEPGRAM_API_KEY", "")
    monkeypatch.setattr(main_module, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))

    with TestClient(main_module.app) as test_client:
        response = test_client.post(
            "/api/audio/tts",
            data={"text": "+99999991001", "voice": "ar-EG-ShakirNeural"},
        )

    assert response.status_code == 200
    assert captured["text"] != "+99999991001"
    assert "plus" in captured["text"].lower()
    assert "9" in captured["text"]
