# AegisTel — Run & Test Guide

One place for every supported way to run AegisTel and to run the test suite.
Also see `DEPLOYMENTS.md` for provider-specific deployment detail and `README.md`
for the architecture and demo walkthrough.

The app is a two-process stack:

| Component | Tech | Port |
|---|---|---|
| Backend | FastAPI + Uvicorn + LangGraph/CrewAI | 8000 (host/container) |
| Frontend | Next.js (standalone) | 3000 (compose) / 7860 (Dockerfile.hf) |

The Next.js `rewrites()` in `frontend/next.config.ts` forwards `/api/*` to the
backend. The target is a **build-time** value:
`process.env.AEGISTEL_BACKEND_URL ?? "http://127.0.0.1:8000"`.

- Local dev / single-container (`Dockerfile.hf`, Render): defaults to
  `127.0.0.1:8000`, which is correct because `start.sh` binds the backend to
  port 8000 inside the same container/host.
- docker-compose (multi-service): passes `AEGISTEL_BACKEND_URL=http://backend:7860`
  as a build arg so the frontend reaches the backend service by its compose
  network name.

---

## Before you run — create your own API keys (required)

The app needs **provider API keys that you must create yourself** from each
platform. They are never bundled or committed (`.env` is gitignored), so every
judge/reviewer creates their own before running. The backend loads keys from a
`.env` file at the **repo root**.

**1. Create keys** at these platforms (free tiers are enough):

| File key | Where to create it | Needed for | Mandatory? |
|---|---|---|---|
| `GROQ_API_KEY` | https://console.groq.com — API Keys | Primary LLM (specialist + auditor + memory) | **Yes** (one of GROQ/OpenRouter/Gemini) |
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey (create a key with the Generative Language API) | QDRANT memory embeddings + memory extraction | **Yes** (memory feeds the verdict) |
| `QDRANT_API_KEY` + `QDRANT_URL` | https://qdrant.tech — create/host a cluster, copy its API key & URL | Vector store for memory context | **Yes** (memory is mandatory) |
| `NOKIA_NAC_API_KEY` | Nokia Network-as-Code on RapidAPI (host `network-as-code.nokia.rapidapi.com`) | The 7 CAMARA telecom checks | No — falls back to sandbox signals |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | LLM fallback tier | Optional |
| `OPENAI_API_KEY` / `CEREBRAS_API_KEY` | platform providers | LLM fallback tiers | Optional |
| `DEEPGRAM_API_KEY` | https://deepgram.com | Neural TTS | Optional — empty falls back to `edge_tts` |

**2. Create the root `.env` from the template, then fill it in:**

```bash
# from the repo root:
cp backend/.env.example .env
# open .env and paste your real keys into the variables above
```

> The backend resolves `.env` from the **repo root** (see `config.py`), so the
> file must be at the root, *not* inside `backend/`. `.gitignore` already
> excludes it, so it will never be committed.

**3. Minimum for a full, live verdict:** `GROQ_API_KEY` (or another LLM key),
`GOOGLE_API_KEY`, `QDRANT_API_KEY` and `QDRANT_URL`, else memory context is
skipped and the app shows `used_fallback`. `NOKIA_NAC_API_KEY` is optional —
without it the CAMARA checks use the built-in sandbox signals.

You only need to do this **once per machine**. The steps below then run the app.

---

## 1. Local development (bare metal)

```bash
# backend (from the repo root, first create root .env per the section above)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

> `requirements.txt` pins the exact validated set (crewai==1.15.8, langchain-*
> 1.3.x/1.5.x, mem0ai==2.0.5, openai==2.50.0, …), so a fresh install reproduces
> the environment this suite was verified against instead of pip backtracking to
> an older, broken crewai release.

- Dashboard: http://localhost:3000
- API docs: http://localhost:8000/docs
- Health: `curl http://localhost:8000/api/health`

## 2. Docker Compose (local, two services)

```bash
docker compose up --build -d
```

- Backend: http://localhost:8000
- Frontend: http://localhost:3000 (proxies `/api/*` to `backend:7860`)

## 3. Single container (Dockerfile.hf — HF Spaces / one Render web service)

```bash
docker build -f Dockerfile.hf -t aegistel .
docker run -p 7860:7860 \
  -e GROQ_API_KEY=... -e GOOGLE_API_KEY=... -e NOKIA_NAC_API_KEY=... \
  aegistel
```

`start.sh` launches the backend (port 8000) then the frontend server on
`$PORT` (default 7860). Health: `curl http://localhost:7860/api/health`.

## 4. Render

Create a **Web Service** from this repo, branch `main`, runtime Docker,
Dockerfile path `Dockerfile.hf`, and set the required env vars as secrets
(`GROQ_API_KEY`, `GOOGLE_API_KEY`, `NOKIA_NAC_API_KEY`, `QDRANT_URL`,
`QDRANT_API_KEY`; others optional — see `DEPLOYMENTS.md`). Then
`https://<service>.onrender.com/api/health`.

Free tier sleeps after ~15 min idle; the first request can take 30–60s to
cold-start. Keep it warm with `submission/keep_render_alive.sh` (cron, `--loop`,
or the bundled systemd timer).

---

## Running the test suite

Python 3.12. The default suite is genuinely offline/network-free — it never
touches a live model or the telecom SDK:

- The Nokia NaC SDK is replaced by an in-memory stub fixture (conftest).
- LLM provider credentials are blanked, so `execute_audit` and the specialist
  crew take their deterministic, network-free fallback (no live model calls,
  so nothing can wedge on a hung or rate-limited upstream).
- mem0's live LLM/embedding extraction is disabled, so memory writes land only
  in a scratch store redirected via `AEGISTEL_MEMORY_PATH`.
- Provider cooldowns are reset between tests.

```bash
cd backend
../venv/bin/python -m pytest tests/ -q     # 89 offline tests + 1 opt-in live test

# Opt-in live behavioral eval (needs real model keys; LLM-vs-deterministic gate):
../venv/bin/python -m pytest tests/test_behavioral_eval.py --run-live
```

> **Live caveat:** `--run-live` performs real LLM and telecom calls. The free
> tiers used by the demo (Groq 8000 TPM, Gemini embeddings, CAMARA sandbox) are
> small, so repeated/large runs can transiently exhaust quota (e.g. Groq
> `413 tokens per minute`, Gemini `RESOURCE_EXHAUSTED`). Such errors are treated
> as retryable rate-limit conditions — the crew cooldowns the model and falls
> back to the next tier or the deterministic contract, and memory writes degrade
> to the local store. Verdicts therefore remain honest even under quota pressure;
> they just may not reflect the top-tier model.

`backend/pytest.ini` ships module-scoped filters that silence unrelated
third-party deprecation warnings (CrewAI, Starlette), so a clean run reports
zero warnings.

Selective runs (fast, for iteration):

```bash
../venv/bin/python -m pytest tests/test_orchestrator.py -q
../venv/bin/python -m pytest tests/test_reconcile_crew_output.py -q
../venv/bin/python -m pytest tests/test_adversarial_drill.py -q
```

### Optional live-LLM end-to-end check

See `submission/E2E_REAL_LLM_TEST.md` for the full procedures (real audit via
`POST /api/v1/audit`, memory persistence with QDRANT + GOOGLE keys, and TTS
verification).

---

## Quick health + smoke checks

```bash
# backend alive (expect active_tool_count=7 on a configured instance)
curl -s <BASE_URL>/api/health

# deterministic audit (no LLM quota; fast)
curl -s -X POST <BASE_URL>/api/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+9715xxxxxxxx","amount":1000,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":25.2,"longitude":55.2},"request_qod_slice":false}'

# adversarial drill (deterministic variant)
curl -s -X POST <BASE_URL>/api/v1/drill/run -H 'Content-Type: application/json' -d '{"use_llm": false}'
```
