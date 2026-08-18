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


def test_memory_engine_prefers_groq_llm_and_falls_back_to_gemini(monkeypatch):
    captured = {}

    class FakeMemory:
        @classmethod
        def from_config(cls, config):
            captured.update(config)
            return cls()

    monkeypatch.setattr(memory_agent, "Memory", FakeMemory)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "groq-test-key")

    engine = memory_agent.NetworkMemoryEngine()

    assert engine.memory is not None
    assert captured["llm"]["provider"] == "groq"
    assert captured["llm"]["config"]["model"] == "openai/gpt-oss-20b"
    assert captured["embedder"]["provider"] == "gemini"

    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    captured.clear()
    engine = memory_agent.NetworkMemoryEngine()
    assert captured["llm"]["provider"] == "gemini"
    assert captured["llm"]["config"]["model"] == settings.GEMINI_MODEL


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
