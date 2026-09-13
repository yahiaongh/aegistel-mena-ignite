import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import crew_specialists
from app.agents import memory_agent
from app.core.config import Settings, settings


def test_prompt_descriptions_stay_compact_for_large_memory_context():
    executed_tool_results = [
        {"name": "check_sim_swap", "swapped": True, "source": "sandbox"},
        {"name": "check_roaming_status", "roamingStatus": "INTERNATIONAL_ROAMING", "source": "sandbox"},
        {"name": "verify_location", "verificationResult": "FALSE", "source": "sandbox"},
        {"name": "check_device_reachability", "reachable": True, "source": "sandbox"},
        {"name": "create_qod_session", "qosStatus": "REQUESTED", "source": "sandbox"},
    ]
    memory_context = [{"text": f"incident #{idx}", "metadata": {"risk_score": "HIGH"}} for idx in range(8)]

    security_description = crew_specialists._build_task_description(
        role="security",
        executed_tool_results=executed_tool_results,
        memory_context=memory_context,
        msisdn="+99999991000",
        amount=120000.0,
        request_qod=True,
    )
    network_description = crew_specialists._build_task_description(
        role="network",
        executed_tool_results=executed_tool_results,
        memory_context=memory_context,
        msisdn="+99999991000",
        amount=120000.0,
        request_qod=True,
    )
    risk_description = crew_specialists._build_task_description(
        role="risk",
        executed_tool_results=executed_tool_results,
        memory_context=memory_context,
        msisdn="+99999991000",
        amount=120000.0,
        request_qod=True,
    )

    assert len(security_description) // 4 < 5000
    assert len(network_description) // 4 < 5000
    assert len(risk_description) // 4 < 5000


def test_memory_engine_builds_qdrant_store_only_when_enabled(monkeypatch):
    """The durable Qdrant mirror is built only when explicitly opted in, and
    never dials out otherwise (a freshly constructed store is inert until used).
    """
    captured = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collection_exists(self, name):
            return True

    monkeypatch.setattr("app.qdrant_store.QdrantClient", FakeQdrantClient)
    monkeypatch.delenv("AEGISTEL_LIVE_MEMORY", raising=False)

    engine = memory_agent.NetworkMemoryEngine()
    assert engine.qdrant.enabled is False
    engine.qdrant.upsert("p1", {"created_at": "2026-01-01T00:00:00Z", "user_id": "1", "text": "t", "metadata": {}})
    assert captured == {}  # no client was built while disabled

    monkeypatch.setenv("AEGISTEL_LIVE_MEMORY", "1")
    engine = memory_agent.NetworkMemoryEngine()
    assert engine.qdrant.enabled is True
    engine.qdrant.upsert("p1", {"created_at": "2026-01-01T00:00:00Z", "user_id": "1", "text": "t", "metadata": {}})
    assert captured.get("url") == settings.QDRANT_URL
    assert captured.get("api_key") == settings.QDRANT_API_KEY
    assert captured.get("timeout") == 5


def test_no_decommissioned_groq_models_anywhere():
    """Groq decommissioned llama-3.3-70b-versatile and llama-3.1-8b-instant on
    2026-08-16; every model reference must be on a supported replacement."""
    for chain in crew_specialists.MODEL_CHAIN.values():
        for model in chain:
            assert "llama-3.3-70b" not in model
            assert "llama-3.1-8b" not in model
    assert "groq/openai/gpt-oss-120b" in crew_specialists.MODEL_CHAIN["specialist"]
    assert "groq/openai/gpt-oss-20b" in crew_specialists.MODEL_CHAIN["specialist"]
    assert "groq/openai/gpt-oss-20b" in crew_specialists.MODEL_CHAIN["auditor"]

    defaults = Settings.model_fields
    assert defaults["LLM_MODEL"].default == "groq/openai/gpt-oss-120b"
    assert defaults["GROQ_MODEL"].default == "openai/gpt-oss-120b"
