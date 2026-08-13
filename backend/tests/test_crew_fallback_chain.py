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
    # The reordered chain prefers the fast reliable provider (OpenRouter)
    # first, so with Groq keys absent the specialist runs on it before Gemini.
    assert any(model.startswith("openrouter/") for _, model in calls if _ == "agent")


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
    # With Groq keys absent, the reordered chain prefers the fast reliable
    # provider (OpenRouter) before falling back to the rate-limit-prone Gemini.
    assert attempted == [
        "openrouter/openai/gpt-4o-mini",
        "openrouter/openai/gpt-4o-mini",
        "openrouter/openai/gpt-4o-mini",
        "gemini/gemini-flash-latest",
        "gemini/gemini-flash-latest",
        "gemini/gemini-flash-latest",
    ]


def test_risk_triggers_qod_provisioning(monkeypatch):
    tool_names = []

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        tool_names.append(tool_name)
        if tool_name == "check_sim_swap":
            return {"name": tool_name, "swapped": True, "source": "sandbox"}
        if tool_name == "verify_location":
            return {"name": tool_name, "verificationResult": "FALSE", "radius_meters": 5000, "source": "sandbox"}
        if tool_name == "check_roaming_status":
            return {"name": tool_name, "roamingStatus": "INTERNATIONAL_ROAMING", "countryIsoCodes": ["HU"], "source": "sandbox"}
        if tool_name == "check_device_reachability":
            return {"name": tool_name, "reachabilityStatus": "DATA_ONLY", "source": "sandbox"}
        if tool_name == "create_qod_session":
            return {"name": tool_name, "sessionId": "qod-123", "qosStatus": "REQUESTED", "qosProfile": "QOS_E", "source": "sandbox"}
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(crew_specialists, "_run_tool_payload", fake_run_tool_payload)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "CEREBRAS_API_KEY", "")

    result = crew_specialists.run_specialist_crew(
        {"msisdn": "+99999991000", "amount": 1000.0, "longitude": 46.7, "latitude": 24.7, "request_qod": False},
        [],
        [],
    )

    # HIGH risk with a low amount and no explicit QoD request must still
    # auto-provision a QoD session (risk-triggered provisioning).
    assert "create_qod_session" in tool_names
    assert result["assessment"]["status"] == "STEP_UP_REQUIRED"
    assert result["assessment"]["qod_session_active"] is True
    assert any("QoD-assisted step-up session was provisioned" in str(result["assessment"].get("reasoning", "")) for _ in [0])


def test_clean_low_amount_does_not_provision_qod(monkeypatch):
    tool_names = []

    def fake_run_tool_payload(tool_name, tool_callable, **kwargs):
        tool_names.append(tool_name)
        if tool_name == "check_sim_swap":
            return {"name": tool_name, "swapped": False, "source": "sandbox"}
        if tool_name == "verify_location":
            return {"name": tool_name, "verificationResult": "TRUE", "radius_meters": 5000, "source": "sandbox"}
        if tool_name == "check_roaming_status":
            return {"name": tool_name, "roamingStatus": "DOMESTIC", "source": "sandbox"}
        if tool_name == "check_device_reachability":
            return {"name": tool_name, "reachabilityStatus": "DATA_ONLY", "source": "sandbox"}
        return {"name": tool_name, "status_code": 200, "source": "sandbox"}

    monkeypatch.setattr(crew_specialists, "_run_tool_payload", fake_run_tool_payload)
    monkeypatch.setattr(crew_specialists.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(crew_specialists.settings, "CEREBRAS_API_KEY", "")

    result = crew_specialists.run_specialist_crew(
        {"msisdn": "+99999991001", "amount": 100.0, "longitude": 46.7, "latitude": 24.7, "request_qod": False},
        [],
        [],
    )

    assert "create_qod_session" not in tool_names
    assert result["assessment"]["status"] == "APPROVED"
    assert result["assessment"]["qod_session_active"] is False
