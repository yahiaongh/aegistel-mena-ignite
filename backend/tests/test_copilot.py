import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_module
from app.copilot_agent import _best_topic, answer  # noqa: E402


# ── Deterministic retrieval (unit level) ───────────────────────────────────────

def test_topic_resolution_for_core_questions():
    assert _best_topic("How do I run an audit?")["id"] == "how_audit"
    assert _best_topic("What does BLOCKED mean?")["id"] == "verdicts"
    assert _best_topic("What are the 7 CAMARA tools?")["id"] == "tools"
    assert _best_topic("How does the drill work?")["id"] == "drill"
    assert _best_topic("Who pays for this?")["id"] == "who_buys"
    assert _best_topic("do you store personal data")["id"] == "security"


def test_topic_single_long_token_still_matches():
    assert _best_topic("what is reachability")["id"] == "reachability"
    assert _best_topic("explain congestion")["id"] == "congestion"


def test_greeting_and_thanks():
    assert answer("hi")["topic"] == "_greeting"
    assert answer("shukran!")["topic"] == "_thanks"


def test_praise_and_mixed_sentiment():
    assert answer("I love this demo")["topic"] == "_praise"
    assert answer("awesome, thanks")["topic"] == "_thanks"


def test_bank_api_question_and_llm_question_resolve():
    assert _best_topic("Can banks call this as an API?")["id"] == "who_buys"
    assert _best_topic("what LLMs do you use?")["id"] == "stack"


def test_typo_tolerant_tier():
    assert _best_topic("hoiw do i runn an odiut?")["id"] == "how_audit"
    assert _best_topic("how does the veridict get explained?")["id"] == "verdicts"


def test_short_fragments_resolve():
    assert _best_topic("audit")["id"] == "how_audit"
    assert _best_topic("buy")["id"] == "who_buys"
    assert _best_topic("buy")["id"] == "who_buys"


def test_account_takeover_and_pricing_questions():
    assert _best_topic("explain account takeover attacks")["id"] == "sim_swap"
    assert _best_topic("how much does a transfer cost")["id"] == "who_buys"
    assert _best_topic("whats the price")["id"] == "who_buys"


def test_msisdn_input_detects_audit_help():
    result = answer("+99999991001")
    assert result["topic"] == "_msisdn"
    assert "Run Audit" in result["answer"]
    assert _best_topic("99999991001")["id"] == "_msisdn"


def test_drill_invitation_routes_to_drill():
    assert _best_topic("run the drill")["id"] == "drill"
    assert _best_topic("start the drill")["id"] == "drill"


def test_ungrounded_questions_stay_fallback():
    assert _best_topic("what is the weather in riyadh?") is None
    assert answer("what is the weather in riyadh?")["topic"] == "_fallback"


def test_weak_exact_token_does_not_block_fuzzy():
    result = answer("can u explane sim swp?")
    assert result["topic"] in ("sim_swap", "explainability")


def test_unknown_question_uses_fallback_and_offers_topics():
    result = answer("can you order me a pizza")
    assert result["topic"] == "_fallback"
    assert len(result["followups"]) >= 3


def test_followup_hint_pins_last_topic():
    result = answer("go deeper", last_topic="how_audit")
    assert "Run an audit" in result["answer"]


def test_answer_shape_and_fallback_flag_when_offline():
    result = answer("How do I run an audit?", enhance=True)
    assert result["topic"] == "how_audit"
    assert result["model"] is None
    assert result["provider"] is None
    assert result["used_fallback"] is True
    assert result["followups"] and result["followups"][0]["topic"]


def test_followups_are_real_topics_and_deduped():
    result = answer("Which tools protect me?")
    for chip in result["followups"]:
        assert chip["topic"] != result["topic"]


# ── HTTP layer ─────────────────────────────────────────────────────────────────

def _chat(client, question, **extra):
    return client.post("/api/copilot/chat", json={"question": question, **extra})


def test_copilot_http_chat():
    with TestClient(main_module.app) as client:
        response = _chat(client, "How do I run an audit?")
    assert response.status_code == 200
    data = response.json()
    assert data["topic"] == "how_audit"
    assert "Run Audit" in data["answer"]


def test_copilot_http_chat_with_history_and_last_topic():
    with TestClient(main_module.app) as client:
        response = _chat(
            client,
            "tell me more",
            history=[{"role": "user", "content": "How does the drill work?"}, {"role": "assistant", "content": "...", "topic": "drill"}],
            last_topic="drill",
        )
    assert response.status_code == 200
    assert response.json()["topic"] in ("_fallback", "drill")


def test_copilot_rejects_empty_question():
    with TestClient(main_module.app) as client:
        response = _chat(client, "   ")
    assert response.status_code == 422