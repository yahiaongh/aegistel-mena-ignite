import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import crew_specialists


def test_model_chain_advances_when_primary_model_fails(monkeypatch):
    calls = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            calls.append(("agent", kwargs.get("llm")))

    class FakeTask:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class FakeCrew:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def kickoff(self):
            if len(calls) < 6:
                raise RuntimeError("429 rate limit")
            return '{"status": "APPROVED", "risk_score": "LOW", "reasoning": "fallback worked"}'

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(crew_specialists, "Agent", FakeAgent)
    monkeypatch.setattr(crew_specialists, "Task", FakeTask)
    monkeypatch.setattr(crew_specialists, "Crew", FakeCrew)
    monkeypatch.setattr(crew_specialists, "_run_tool_payload", fake_run_tool_payload)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "GOOGLE_API_KEY", "google-key")
    monkeypatch.setattr(crew_specialists.settings, "CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setattr(crew_specialists.settings, "OPENROUTER_API_KEY", "openrouter-key")

    def fake_synth(*args, **kwargs):
        return {
            "assessment": {
                "status": "APPROVED",
                "risk_score": "LOW",
                "sim_swap_detected": False,
                "last_sim_swap_date": None,
                "location_verification_match": True,
                "roaming_status": "DOMESTIC",
                "roaming_country": None,
                "qod_session_active": False,
                "qod_profile": None,
                "reasoning": "deterministic",
                "recommended_action": "allow",
            },
            "trace": [],
        }

    monkeypatch.setattr(crew_specialists, "synthesize_specialist_assessment", fake_synth)

    result = crew_specialists.run_specialist_crew(
        {"msisdn": "+99999991000", "amount": 100000.0, "longitude": 46.7, "latitude": 24.7, "request_qod": True},
        [],
        [],
    )

    assert result["used_fallback"] is False
    assert result["assessment"]["status"] == "APPROVED"
    assert any(model.startswith("gemini/") for _, model in calls if _ == "agent")


def test_model_chain_runs_without_groq_when_other_providers_are_configured(monkeypatch):
    calls = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            calls.append(("agent", kwargs.get("llm")))

    class FakeTask:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class FakeCrew:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def kickoff(self):
            return '{"status": "APPROVED", "risk_score": "LOW", "reasoning": "fallback worked"}'

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(crew_specialists, "Agent", FakeAgent)
    monkeypatch.setattr(crew_specialists, "Task", FakeTask)
    monkeypatch.setattr(crew_specialists, "Crew", FakeCrew)
    monkeypatch.setattr(crew_specialists, "_run_tool_payload", fake_run_tool_payload)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "GOOGLE_API_KEY", "google-key")
    monkeypatch.setattr(crew_specialists.settings, "CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setattr(crew_specialists.settings, "OPENROUTER_API_KEY", "openrouter-key")

    def fake_synth(*args, **kwargs):
        return {
            "assessment": {
                "status": "APPROVED",
                "risk_score": "LOW",
                "sim_swap_detected": False,
                "last_sim_swap_date": None,
                "location_verification_match": True,
                "roaming_status": "DOMESTIC",
                "roaming_country": None,
                "qod_session_active": False,
                "qod_profile": None,
                "reasoning": "deterministic",
                "recommended_action": "allow",
            },
            "trace": [],
        }

    monkeypatch.setattr(crew_specialists, "synthesize_specialist_assessment", fake_synth)

    result = crew_specialists.run_specialist_crew(
        {"msisdn": "+99999991000", "amount": 100000.0, "longitude": 46.7, "latitude": 24.7, "request_qod": True},
        [],
        [],
    )

    assert result["used_fallback"] is False
    assert result["assessment"]["status"] == "APPROVED"
    assert any(model.startswith("gemini/") for _, model in calls if _ == "agent")


def test_model_chain_retries_each_supported_model_once(monkeypatch):
    calls = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            calls.append(("agent", kwargs.get("llm")))

    class FakeTask:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class FakeCrew:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def kickoff(self):
            if len(calls) < 6:
                raise RuntimeError("429 rate limit")
            return '{"status": "APPROVED", "risk_score": "LOW", "reasoning": "fallback worked"}'

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(crew_specialists, "Agent", FakeAgent)
    monkeypatch.setattr(crew_specialists, "Task", FakeTask)
    monkeypatch.setattr(crew_specialists, "Crew", FakeCrew)
    monkeypatch.setattr(crew_specialists, "_run_tool_payload", fake_run_tool_payload)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "GOOGLE_API_KEY", "google-key")
    monkeypatch.setattr(crew_specialists.settings, "CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setattr(crew_specialists.settings, "OPENROUTER_API_KEY", "openrouter-key")

    def fake_synth(*args, **kwargs):
        return {
            "assessment": {
                "status": "APPROVED",
                "risk_score": "LOW",
                "sim_swap_detected": False,
                "last_sim_swap_date": None,
                "location_verification_match": True,
                "roaming_status": "DOMESTIC",
                "roaming_country": None,
                "qod_session_active": False,
                "qod_profile": None,
                "reasoning": "deterministic",
                "recommended_action": "allow",
            },
            "trace": [],
        }

    monkeypatch.setattr(crew_specialists, "synthesize_specialist_assessment", fake_synth)

    result = crew_specialists.run_specialist_crew(
        {"msisdn": "+99999991000", "amount": 100000.0, "longitude": 46.7, "latitude": 24.7, "request_qod": True},
        [],
        [],
    )

    assert result["used_fallback"] is False
    assert result["assessment"]["status"] == "APPROVED"
    attempted = [model for _, model in calls if _ == "agent"]
    assert attempted == [
        "gemini/gemini-flash-latest",
        "gemini/gemini-flash-latest",
        "gemini/gemini-flash-latest",
        "openrouter/openai/gpt-4o-mini",
        "openrouter/openai/gpt-4o-mini",
        "openrouter/openai/gpt-4o-mini",
    ]
