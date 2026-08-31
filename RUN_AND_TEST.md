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

## 1. Local development (bare metal)

```bash
# backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env 2>/dev/null || true   # or export the vars from your root .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

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

Python 3.12. The suite is offline/network-free: the Nokia NaC SDK and LLM calls
are stubbed, memory is redirected to a scratch file, and provider cooldowns are
reset between tests.

```bash
cd backend
../venv/bin/python -m pytest tests/ -q     # 87 tests, no live keys needed
```

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
