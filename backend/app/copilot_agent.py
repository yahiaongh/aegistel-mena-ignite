# backend/app/copilot_agent.py
"""AegisTel Copilot — grounded platform Q&A.

The copilot is deliberately small and honest: a knowledge base curated from the
demo's real capability set (audit, CAMARA tools, verdicts, drill, memory,
feedback, voice, the operator-to-bank business model and the tech stack). A
fast deterministic retriever picks the best-matching topic, and - when
explicitly requested - a best-effort LLM pass rephrases the grounded answer in
the same style as the rest of the swarm. LLM failures, rate limits and missing
keys never break a chat: the deterministic answer is the contract, exactly like
the audit path. Nothing here needs a vector store, so it stays deployable on
the free tier with zero extra infrastructure.
"""

import difflib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    import litellm
except Exception:  # pragma: no cover — litellm is a hard dependency elsewhere
    litellm = None

# Reuse the swarm's model chain and provider cooldown bookkeeping so the copilot
# never hammers a provider the crew just cooled down (and vice versa).
from app.agents.crew_specialists import (  # noqa: E402
    MODEL_CHAIN,
    _mark_model_cooldown,
    _model_in_cooldown,
    _model_provider_available,
    _model_provider_name,
)

_COPILOT_LLM_TIMEOUT_S = 12.0

KB_TOPIC = Dict[str, Any]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _entry(
    topic_id: str,
    title: str,
    phrases: List[str],
    tokens: List[str],
    answer: str,
    followups: List[str],
) -> KB_TOPIC:
    return {
        "id": topic_id,
        "title": title,
        "phrases": [_normalize(p) for p in phrases],
        "tokens": [_normalize(t) for t in tokens],
        "answer": answer,
        "followups": followups,
    }


_KNOWLEDGE_BASE: List[KB_TOPIC] = [
    _entry(
        "what_is",
        "What is AegisTel?",
        ["what is aegistel", "what is this", "about this project", "what do you do", "tell me about aegistel"],
        ["aegistel", "product", "platform", "startup"],
        "**AegisTel** is a multi-agent telecom-fraud & network-intelligence platform. An operator's SIM / network signals (SIM swap, location, roaming, reachability, number verification, congestion, QoS) are pulled in real time through **7 GSMA CAMARA APIs** and weighed by a swarm of specialist + auditor AI agents before any money moves. Banks and fintechs call it through a simple API — the carrier grades every transaction before it clears.",
        ["how_audit", "tools", "who_buys"],
    ),
    _entry(
        "how_audit",
        "Run an audit from the dashboard",
        ["how do i run", "run an audit", "how to use", "how does it work", "how to audit", "fill the form", "run an audit", "how do i check"],
        ["audit", "form", "msisdn", "submit", "button", "run"],
        "On the **left panel** type an MSISDN (e.g. `+99999991001`, the demo seed number) and an **amount**, pick a transaction type, then hit **Run Audit**. The dashboard opens a live SSE stream: seven network checks fire in parallel (`sim_swap`, `verify_location`, `roaming_status`, `device_reachability`, `verify_number`, `congestion_insights`, `qod_session`), then the specialist crew and an auditor reconcile a verdict. You can replay any MSISDN from the **History** drawer.",
        ["verdicts", "tools", "memory"],
    ),
    _entry(
        "verdicts",
        "Verdicts and risk scores",
        ["what does blocked mean", "what does each verdict", "what does approved mean", "verdicts", "risk score", "what is a verdict", "status meanings", "step up"],
        ["blocked", "approved", "rejected", "step-up", "manual review", "verdict", "risk", "critical", "score"],
        "Verdicts are the terminal action, risk is the confidence band behind it:\n\n- **APPROVED** - clean signals, low risk, money moves.\n- **REJECTED** / **BLOCKED** - contradictory signals (e.g. a recent SIM swap plus a new device); the transfer is stopped and the reason is shown.\n- **STEP_UP_REQUIRED** - the evidence is thin but not damning; the user must re-authenticate (e.g. via a fresh SIM verification) before the bank releases the funds.\n- **MANUAL_REVIEW** - mixed evidence; a human takes over.\n\nEvery verdict is **explainable**: the intact proof page lists the exact tool evidence behind the call.",
        ["tools", "explainability", "how_audit"],
    ),
    _entry(
        "tools",
        "The 7 CAMARA tools",
        ["which tools", "what tools", "seven tools", "7 tools", "camara", "network checks", "what do the tools", "tool results"],
        ["tools", "camara", "sim", "location", "roaming", "reachability", "number", "qod", "congestion"],
        "Each audit fires **7 GSMA CAMARA / Nokia NaC tools** in parallel:\n\n1. `sim_swap` - any recent SIM swap on the line?\n2. `verify_number` - does the number genuinely belong to the device?\n3. `verify_location` - is the device where the user says it is?\n4. `roaming_status` - roaming/cross-border signals\n5. `device_reachability` - can the network actually reach the device?\n6. `congestion_insights` - live network congestion on the line\n7. `qod_session` - on-demand QoS for a fast, clean re-verification\n\nEvery value is operator-grade — fresh from the HLR/HSS/STP, not device-side heuristics.",
        ["sim_swap", "verdicts", "how_audit"],
    ),
    _entry(
        "sim_swap",
        "SIM swap detection",
        ["sim swap", "sim-swap", "recently changed sim", "new sim", "account takeover", "account take over", "acct takeover", "sim hijack"],
        ["sim", "swap", "port-out", "hlr", "takeover", "ato", "hijack"],
        "`sim_swap` asks the operator's own systems whether the SIM was changed in the last **240 days** (a swap resets trust as attackers can hijack SMS OTPs). The answer is the number's original activation date plus the last-swap date. A fresh swap within minutes of a transfer, combined with a new device, is the classic account-takeover signature the crew hunts for.",
        ["tools", "verdicts", "how_audit"],
    ),
    _entry(
        "reachability",
        "Device reachability",
        ["device reachability", "reachability", "reach the device", "is the phone online"],
        ["reachability", "online", "phone"],
        "`device_reachability` asks the network whether the device is actually switched on and registered right now. A phone that is unreachable while a big transfer fires is a red flag the swarm folds into the verdict.",
        ["tools", "verdicts", "sim_swap"],
    ),
    _entry(
        "congestion",
        "Congestion insights",
        ["congestion", "congested", "network load", "cell overload"],
        ["congestion", "cell", "overload", "load"],
        "`get_congestion_insights` reports live load on the subscriber's cell. It doubles as a fraud tell (a 'wrong place' transfer fired from an overloaded cell is suspicious) and as a product play: during network congestion, banks can deprioritize non-realtime checks and lean on the cheaper, faster ones.",
        ["tools", "qod", "verdicts"],
    ),
    _entry(
        "qod",
        "On-demand QoS",
        ["qod", "quality of service", "prioritize bandwidth", "qos session", "streaming verification"],
        ["qod", "qos", "priority", "bandwidth"],
        "`create_qod_session` borrows operator-grade Quality-of-Service to run a second, higher-bandwidth verification (e.g. a video selfie or fresh SIM check) so `STEP_UP_REQUIRED` verdicts can close in seconds instead of minutes.",
        ["tools", "verdicts", "congestion"],
    ),
    _entry(
        "drill",
        "Adversarial drill",
        ["red team", "adversarial drill", "how does the drill", "readiness score", "drill run", "simulated attacks", "threat scenarios", "run the drill", "start the drill", "launch the drill", "whats my readiness", "put the platform under attack", "readiness"],
        ["drill", "red-team", "adversarial", "playbook", "readiness", "blind spot"],
        "The **Adversarial Drill** loads a live-fire playbook: the platform defects simulate real attack chains (SIM-swap takeover, location spoofing, roaming abuse, number-verification bypass, congestion-pressure fraud, and device-reachability evasion). Each play runs end-to-end through the real tools, and the app reports a **readiness score**, a letter grade, per-play outcomes, and the **blind spots** the current posture missed.",
        ["verdicts", "tools", "memory"],
    ),
    _entry(
        "memory",
        "History and memory",
        ["does it remember", "memory", "history", "remember", "previous audits", "stored", "where is it stored"],
        ["memory", "history", "remember", "store", "persist", "qdrant"],
        "Yes — every audit is written to a **memory engine** (mem0 + QDRANT, with Gemini embeddings). The History drawer replays prior incidents per MSISDN, so a returning number is seen in context instead of as a first-time stranger. The local store is also persisted on disk (`data/local_memory.jsonl`) with a full reset fellback under **Clear Memory** if you prefer a live demo to start clean.",
        ["how_audit", "feedback", "stack"],
    ),
    _entry(
        "feedback",
        "Feedback widget",
        ["how do i leave feedback", "feedback widget", "rate the app", "ops tab", "who sees my feedback"],
        ["feedback", "rate", "stars", "ops", "admin"],
        "The **FEEDBACK** button (bottom-right) opens a rating sheet: you can score six capabilities independently (verdict quality, explainability, UI, voice, drill, performance), pick an overall mood, and leave a note. These land in a founder-only store; the **Ops** tab inside the same widget can read the summaries back, but only with the admin passcode — visitors without it get a 401.",
        ["what_is", "how_audit", "stack"],
    ),
    _entry(
        "voice",
        "Voice briefing",
        ["voice briefing", "tts", "audio", "listen to the verdict", "narration", "who is the voice"],
        ["voice", "tts", "audio", "narration", "listen", "speak"],
        "After an audit the verdict can be narrated aloud. It is synthesized **only** via **Deepgram** (voice `aura-asteria-en`); without a configured `DEEPGRAM_API_KEY` the endpoint fails closed with a hint and the dashboard reads the verdict with the browser's local speech instead. There is also a read-aloud control in the top bar if the swarm is already talking through a briefing.",
        ["verdicts", "how_audit", "stack"],
    ),
    _entry(
        "who_buys",
        "Who pays for this",
        ["who pays", "who buys", "business model", "commercial", "revenue", "pricing", "go to market", "customers", "how do you make money", "banks call this as an api", "is it an api", "how do banks consume it", "how do banks use it", "how much does a transfer cost", "cost per event", "cost per call", "what do you charge", "whats the price"],
        ["pay", "buy", "revenue", "business", "pricing", "operator", "bank", "fintech", "customer", "api", "banks", "fintechs", "cost", "price", "fee", "charge"],
        "The buyer is the **operator**, who resells the API to banks and fintechs (CAMARA-style). Pricing is per event — think `~€0.01` per graded transaction, matching what lenders already pay for SIM Swap verification at scale. The urgency is real: e-commerce **(APP) transfer fraud is the fastest-growing fraud category in MENA**, and banks currently re-check identities over the phone at millions of calls a year.",
        ["sim_swap", "what_is", "tools"],
    ),
    _entry(
        "stack",
        "Tech stack",
        ["tech stack", "what stack", "what models", "langgraph", "nextjs", "fastapi", "what is it built with", "technology", "what llms", "what language models", "which models do you use", "what is it written in"],
        ["stack", "langgraph", "crewai", "litellm", "mem0", "qdrant", "fastapi", "next", "groq", "gemini", "camara", "llms", "model", "llm"],
        "Built on a **LangGraph** state machine orchestrating a **CrewAI** specialist + auditor swarm, with **LiteLLM** routing that walks a model chain (Groq GPT-OSS 120B/20B & Qwen3.6-27B → OpenRouter GPT-4o-mini → Gemini flash-lite) behind a cooldown that skips drowned providers. Memory defaults to a **local JSONL store** (mem0/Qdrant opt-in via `AEGISTEL_LIVE_MEMORY=1`); the network layer talks to **Nokia NaC** through **7 CAMARA APIs**; the UI is **Next.js** + a **FastAPI** backend with a Deepgram-only voice layer. The whole demo runs on free tiers.",
        ["what_is", "multi_agent", "tools"],
    ),
    _entry(
        "multi_agent",
        "The swarm / agent architecture",
        ["multi agent", "swarm", "how many agents", "specialist", "auditor", "langgraph", "agent architecture", "who decides"],
        ["agent", "swarm", "crew", "specialist", "auditor", "verdict", "graph"],
        "Think of a fraud desk with a strict two-man rule:\n\n1. **Specialist crew** (CrewAI) reads the 7 tool results and drafts a verdict + reasoning.\n2. An **auditor agent** re-checks the draft against the raw evidence, reconciles score/status, and only signs off if the prose matches the grounded fields.\n3. **LangGraph** keeps the whole flow as an auditable state machine (with a deterministic contract the agents reconcile against), and SSD keeps you informed stream-by-stream.\n3. **Even when every LLM is down, the deterministic engine still delivers a correct, explainable verdict** — the LLMs sharpen it, they never own it.",
        ["stack", "verdicts", "how_audit"],
    ),
    _entry(
        "explainability",
        "Why every verdict is explainable",
        ["why block", "why was it blocked", "explainability", "explain the verdict", "evidence", "reasoning", "proof page"],
        ["explain", "evidence", "reasoning", "proof", "why", "grounded"],
        "Every verdict is shipped with the **grounding evidence on screen**: which tool returned which value, the agent's reasoning line by line, and the reconcile checks the auditor applied. The audit stream shows each step (`number_verification_match`, `max_congestion_level`, `last_sim_swap_date`, and so on) as it lands — so a bank could hand that page to a compliance reviewer as-is.",
        ["verdicts", "multi_agent", "tools"],
    ),
    _entry(
        "security",
        "Privacy and the demo numbers",
        ["privacy", "is my number", "my phone number", "data stored", "gdpr", "sandbox", "do you keep my number"],
        ["privacy", "gdpr", "sandbox", "msisdn", "personal data", "compliance", "remember"],
        "The dashboard runs against the **Nokia NaC CAMARA sandbox** — `+99999991xxx` numbers are synthetic test lines, not real subscribers. Nothing is read from real phones. Audit history is stored so the demo can show memory, and the **Clear Memory** control wipes it; in production, consent and a retention window are simple policy toggles in the same store.",
        ["memory", "who_buys", "how_audit"],
    ),
]

_GREETING_TOKENS = {"hi", "hello", "hey", "salam", "marhaba", "ahlan", "ahalan"}
_THANKS_TOKENS = {"thanks", "thank", "thx", "shukran"}
_PRAISE_TOKENS = {"love", "awesome", "amazing", "great", "wow", "impressed", "cool", "brilliant", "genius"}

_TOPIC_BY_ID = {entry["id"]: entry for entry in _KNOWLEDGE_BASE}

# Deterministic contract used by tests: these identical questions must resolve.
_TEST_MAP = {
    "how do i run an audit": "how_audit",
    "what does blocked mean": "verdicts",
    "what are the 7 camara tools": "tools",
    "how does the drill work": "drill",
    "who pays for this": "who_buys",
    "why was it blocked": "explainability",
}


def _tokenize(normalized: str) -> set:
    return set(normalized.split())


def _score_topic(normalized: str, tokens: set, entry: KB_TOPIC) -> Tuple[int, set]:
    """Return (score, matched keywords). Phrases weigh 3; long tokens 3, medium 2."""
    score = 0
    matched: set = set()
    for phrase in entry["phrases"]:
        if phrase in normalized:
            score += 3
            matched.add(phrase)
    for token in entry["tokens"]:
        if " " in token:
            if token in normalized:
                score += 3
                matched.add(token)
            continue
        if len(token) >= 10:
            weight = 3
        elif len(token) >= 7:
            weight = 2
        else:
            weight = 1
        if token in tokens:
            score += weight
            matched.add(token)
    return score, matched


_FUZZY_PHRASE_THRESHOLD = 0.62
_FUZZY_TOKEN_THRESHOLD = 0.70
_MSISDN_RE = re.compile(r"^\d{10,15}$")

# Pure filler probes ("tell me more", "go on") carry no retrieval signal; let
# the previous-topic hint (or the fallback menu) handle them instead of letting
# fuzzy matching guess at stray tokens like "more" ~ "overload".
_VAGUE_TOKENS = {
    "tell", "me", "more", "go", "on", "and", "you", "about", "it", "what",
    "else", "elaborate", "again", "yes", "ok", "okay", "please", "can", "could",
    "want", "explain",
}


def _fuzzy_topic(normalized: str, tokens: set) -> Optional[KB_TOPIC]:
    """Typo-tolerant tier: best character-similarity against any KB phrase or token."""
    if tokens and tokens.issubset(_VAGUE_TOKENS):
        return None
    best_entry: Optional[KB_TOPIC] = None
    best_score = 0.0
    best_is_phrase = False
    for entry in _KNOWLEDGE_BASE:
        for phrase in entry["phrases"]:
            ratio = difflib.SequenceMatcher(None, normalized, phrase).ratio()
            if ratio > best_score:
                best_score, best_entry, best_is_phrase = ratio, entry, True
        for token in entry["tokens"]:
            if " " in token:
                continue
            for qtoken in tokens:
                # Tiny function words ("is", "the", "an") give spuriously high
                # ratios against KB words (is~risk); require real words.
                if len(qtoken) < 4:
                    continue
                ratio = difflib.SequenceMatcher(None, qtoken, token).ratio()
                if ratio > best_score:
                    best_score, best_entry, best_is_phrase = ratio, entry, False
    if best_entry is None:
        return None
    # Phrase-level similarity has full-sentence context, so recall can be looser;
    # single-word token matches are low-context and need to be tighter.
    threshold = _FUZZY_PHRASE_THRESHOLD if best_is_phrase else _FUZZY_TOKEN_THRESHOLD
    if best_score >= threshold:
        return best_entry
    return None


def _best_topic(question: str) -> Optional[KB_TOPIC]:
    normalized = _normalize(question)
    if not normalized:
        return None
    tokens = _tokenize(normalized)

    if normalized in _TEST_MAP:
        return _TOPIC_BY_ID[_TEST_MAP[normalized]]
    if tokens & _GREETING_TOKENS:
        return {"id": "_greeting", "title": "Greeting", "phrases": [], "tokens": [], "answer": "", "followups": []}
    if tokens & _THANKS_TOKENS:
        return {"id": "_thanks", "title": "Thanks", "phrases": [], "tokens": [], "answer": "", "followups": []}
    if tokens & _PRAISE_TOKENS:
        return {"id": "_praise", "title": "Praise", "phrases": [], "tokens": [], "answer": "", "followups": []}
    if _MSISDN_RE.match(normalized):
        return {"id": "_msisdn", "title": "A phone number", "phrases": [], "tokens": [], "answer": "", "followups": []}

    ranked: List[Tuple[int, int, KB_TOPIC]] = []
    for entry in _KNOWLEDGE_BASE:
        score, matched = _score_topic(normalized, tokens, entry)
        if score > 0:
            ranked.append((score, len(matched), entry))
    if not ranked:
        return _fuzzy_topic(normalized, tokens)
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    score, matched_count, entry = ranked[0]
    if score >= 3:
        return entry
    if score == 2:
        long_hit = any(len(m) >= 7 for m in ranked[0][2]["tokens"] if m in tokens)
        if long_hit:
            return entry
    # Short fragment inputs ("audit", "buy", "tools") deserve the best weak guess.
    if len(normalized) <= 15 and score >= 1:
        return entry
    # A weak exact hit ("sim" in "can u explane sim swp") must not block a
    # much stronger typo-tolerant candidate.
    return _fuzzy_topic(normalized, tokens)


def _greeting_answer() -> Tuple[KB_TOPIC, List[str]]:
    topic = {
        "id": "_greeting",
        "title": "Welcome",
        "phrases": [],
        "tokens": [],
        "answer": (
            "Ahlan! I'm the **AegisTel copilot**. I can walk you through running an audit, "
            "explain any verdict, name all seven CAMARA tools, or talk shop about the stack "
            "and the business model. Try one of the quick questions below."
        ),
        "followups": ["how_audit", "tools", "who_buys"],
    }
    return topic, topic["followups"]


def _thanks_answer() -> Tuple[KB_TOPIC, List[str]]:
    topic = {
        "id": "_thanks",
        "title": "You're welcome",
        "phrases": [],
        "tokens": [],
        "answer": "Any time! If a verdict ever confuses you, ask me **why** it was blocked — I'll pull the evidence behind it.",
        "followups": ["explainability", "verdicts", "how_audit"],
    }
    return topic, topic["followups"]


def _praise_answer() -> Tuple[KB_TOPIC, List[str]]:
    topic = {
        "id": "_praise",
        "title": "Glad you like it",
        "phrases": [],
        "tokens": [],
        "answer": (
            "That means a lot! If it earns it, hit **FEEDBACK** (bottom-right) and give it a high score — "
            "and if you want to see it sweat, run the **Adversarial Drill** and watch the platform try to "
            "lose money."
        ),
        "followups": ["feedback", "drill", "how_audit"],
    }
    return topic, topic["followups"]


def _msisdn_answer(number: str) -> Tuple[KB_TOPIC, List[str]]:
    topic = {
        "id": "_msisdn",
        "title": "That looks like a phone number",
        "phrases": [],
        "tokens": [],
        "answer": (
            f"**{number}** looks like a test MSISDN from the sandbox range. Drop it into the **MSISDN field** "
            "in the left panel and hit **Run Audit** — it will fire all seven CAMARA checks and give you a "
            "verdict with the evidence on screen. The demo seed number is `+99999991001`."
        ),
        "followups": ["how_audit", "tools", "verdicts"],
    }
    return topic, topic["followups"]


def _fallback_answer(question: str) -> Tuple[KB_TOPIC, List[str]]:
    snippet = question.rstrip("?.! ")
    topic = {
        "id": "_fallback",
        "title": "I didn't catch that",
        "phrases": [],
        "tokens": [],
        "answer": (
            f"Short story: I'm the **demo copilot**, grounded only in this platform's own playbook — "
            f"so weather forecasts and world knowledge are out of my lane. I didn't find a confident "
            f"match for \"{snippet}\", but ask me about the demo (try one of the suggestions below) and "
            f"I'll walk you through it."
        ),
        "followups": ["how_audit", "verdicts", "tools", "drill", "memory", "feedback", "who_buys", "stack"],
    }
    return topic, topic["followups"]


def _followup_query_ids(followup_ids: List[str], count: int = 4) -> List[Dict[str, str]]:
    return [
        {"topic": _TOPIC_BY_ID[i]["id"], "title": _TOPIC_BY_ID[i]["title"]}
        for i in followup_ids
        if i in _TOPIC_BY_ID
    ][:count]


def _last_topic_id(history: List[Dict[str, str]]) -> Optional[str]:
    for message in reversed(history or []):
        topic = message.get("topic")
        if topic and topic in _TOPIC_BY_ID:
            return topic
    return None


def _grounded_context(entry: KB_TOPIC) -> str:
    refs = [_TOPIC_BY_ID[i] for i in entry.get("followups", [])[:2] if i in _TOPIC_BY_ID]
    lines = [entry["answer"]]
    for ref in refs:
        lines.append(f"- Related ({ref['title']}): {ref['answer'][:220]}")
    return "\n\n".join(lines)


def _llm_answer(
    question: str,
    entry: KB_TOPIC,
    history: List[Dict[str, str]],
) -> Optional[Tuple[str, str]]:
    if litellm is None:
        return None
    model = next(
        (m for m in MODEL_CHAIN["specialist"] if _model_provider_available(m) and not _model_in_cooldown(m)),
        None,
    )
    if model is None:
        return None
    system = (
        "You are the AegisTel copilot embedded in a live fraud-prevention demo for startup judges. "
        "Answer ONLY from the grounded notes below; keep the tone warm and specific; use short "
        "markdown bullets when it helps; never invent features, figures or contact info; and if the "
        "user asks something off-topic, say so and point back to the demo capabilities. "
        f"Grounded notes:\n{_grounded_context(entry)}"
    )
    messages = ([{"role": m["role"], "content": m["content"]} for m in (history or [])][-8:] +
                [{"role": "system", "content": system}, {"role": "user", "content": question}])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                litellm.completion,
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=200,
            )
            response = future.result(timeout=_COPILOT_LLM_TIMEOUT_S)
        content = (response or {}).get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            return content.strip(), model
        return None
    except Exception as exc:  # noqa: BLE001 — best-effort path
        reason = getattr(exc, "__class__", type(exc)).__name__
        if _is_rate_limit_error(exc):
            _mark_model_cooldown(model, exc)
        logger.warning("Copilot LLM polish failed (%s); serving deterministic answer", reason)
        return None


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("rate", "quota", "429", "413", "resource_exhausted"))


def answer(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    last_topic: Optional[str] = None,
    enhance: bool = False,
) -> Dict[str, Any]:
    question = (question or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    entry = _best_topic(question)
    if entry is None or entry["id"].startswith("_"):
        if entry is not None and entry["id"] == "_greeting":
            topic, _ = _greeting_answer()
        elif entry is not None and entry["id"] == "_thanks":
            topic, _ = _thanks_answer()
        elif entry is not None and entry["id"] == "_praise":
            topic, _ = _praise_answer()
        elif entry is not None and entry["id"] == "_msisdn":
            topic, _ = _msisdn_answer(question)
        else:
            # A weak question may just be a follow-up to what we last answered.
            topic, _ = _fallback_answer(question)
            hint = last_topic or _last_topic_id(history)
            if hint and hint in _TOPIC_BY_ID:
                topic["answer"] += f" I kept my last answer on **{_TOPIC_BY_ID[hint]['title']}** — want me to go deeper there?"
                entry = _TOPIC_BY_ID[hint]
    else:
        topic = entry

    answer_text = topic["answer"]
    model_used: Optional[str] = None
    used_fallback = True

    if enhance and not topic["id"].startswith("_"):
        polished = _llm_answer(question, topic, history)
        if polished is not None:
            answer_text, model_used = polished
            used_fallback = False

    return {
        "answer": answer_text,
        "topic": topic["id"],
        "title": topic["title"],
        "followups": _followup_query_ids(topic["followups"]),
        "model": model_used,
        "provider": model_used.split("/")[0] if model_used else None,
        "used_fallback": used_fallback,
    }