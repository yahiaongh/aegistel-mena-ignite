# AegisTel MENA Ignite — Deployment Guide

Deployment options for AegisTel, covering local development, Docker, Hugging Face Spaces, and Render. The application is a two-process stack:

| Component | Tech | Port | Image |
|---|---|---|---|
| Backend | FastAPI + Uvicorn (LangGraph/CrewAI agents) | 7860 (container) / 8000 (host) | `backend/Dockerfile` |
| Frontend | Next.js (standalone output) | 3000 | `frontend/Dockerfile` |

For single-image deployments (Hugging Face Spaces, single Render Web Service), use the root `Dockerfile.hf` which builds both processes and launches them via `start.sh`.

---

## 1. Local development (bare metal)

Backend (see `backend/`):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env ./.env            # or export the variables from the repo root .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend (see `frontend/`):

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Health check: `curl http://localhost:8000/api/health` → `{"status": "ok", ...}`.

## 2. Docker Compose (local)

```bash
docker compose up --build
```

- Backend available at `http://localhost:8000`
- Frontend available at `http://localhost:3000`
- The backend's Next.js rewrite (`next.config.ts`) forwards `/api/*` to `127.0.0.1:8000`.

## 3. Hugging Face Spaces

The root `Dockerfile.hf` is a multi-stage build (Next.js standalone + Python runtime) that serves both the API and the frontend from one container on the Space's port (7860).

- **SDK:** Docker
- **Port:** 7860 (already default in `Dockerfile.hf`)
- **User:** the backend Dockerfile uses UID 1000; HF Spaces require a non-root user.

Set the env vars from the table below as **Secrets** in the Space settings. `HF_TOKEN` is only needed for model-hosting/private endpoints.

## 4. Render

Two supported topologies. In both cases, point the service at this repository and the `main` branch.

### 4.1 Blueprint (Infrastructure as Code)

Create `render.yaml` at the repo root with the services you want, e.g.:

```yaml
services:
  - type: web
    name: aegistel
    runtime: docker
    repo: https://github.com/yahiaongh/aegistel-mena-ignite
    branch: main
    dockerfilePath: ./Dockerfile.hf
    plan: free
    autoDeploy: true
    envVars:
      - key: GROQ_API_KEY
        sync: false
      - key: GOOGLE_API_KEY
        sync: false
      - key: OPENROUTER_API_KEY
        sync: false
      # ... add the remaining variables from the table below
```

Render reads `render.yaml` from the default branch when you create a Blueprint from the dashboard.

### 4.2 Dashboard (manual)

1. New → **Web Service** → connect this GitHub repo.
2. **Branch:** `main`.
3. **Runtime:** Docker. If deploying the single image, set **Dockerfile path** to `Dockerfile.hf`; if deploying the backend alone use `backend/Dockerfile`.
4. **Instance type:** Free tier is fine for demos; the free instance sleeps after inactivity.
5. Set the environment variables listed below in the service's **Environment** tab.
6. Deploy, then hit `https://<service>.onrender.com/api/health`.

### Will Render update automatically after a push?

- **Yes, automatically** — if the service was connected to this GitHub repo (branch `main`) and its **Auto-Deploy** setting is enabled (Render's default is *Automatic*). Every push to `main` then triggers a new deploy.
- **No** — if Auto-Deploy was switched to *Manual* (or *Disabled*). You'd then click **Manual Deploy → Deploy latest commit** from the service dashboard.
- For a Blueprint-created service, `autoDeploy: true` (the default) enables the same behavior.

You can verify the current setting on the service's **Settings** page under *Deploy hooks / Auto-Deploy*.

### 4.3 Render environment variables

Use the `render.yaml` `sync: false` pattern or dashboard secrets for anything sensitive. Reference values are defined in `backend/app/core/config.py`.

| Variable | Required | Notes |
|---|---|---|
| `GROQ_API_KEY` | optional | Primary specialist/auditor LLM provider |
| `GOOGLE_API_KEY` | optional | Gemini; free-tier quota is rate-limited, hence lower chain priority |
| `OPENROUTER_API_KEY` | optional | Fast reliable fallback, preferred before Gemini in `MODEL_CHAIN` |
| `CEREBRAS_API_KEY` | optional | Additional provider |
| `OPENAI_API_KEY` | optional | Additional provider |
| `DEEPGRAM_API_KEY` | optional | TTS; falls back to `edge_tts` when absent |
| `NOKIA_NAC_API_KEY` | required for live CAMARA calls | Network-as-Code (RapidAPI) key |
| `NOKIA_CAMARA_BASE_URL` | optional | Override NaC base URL |
| `QDRANT_URL` / `QDRANT_API_KEY` | optional | Persistent memory backend; falls back to local store |
| `GEMINI_MODEL` | optional | Default `gemini-flash-latest` |
| `GROQ_MODEL` | optional | Default `llama-3.3-70b-versatile` |
| `FRONTEND_ORIGIN` | optional | CORS allowlist; defaults to `http://localhost:3000` — **set to your frontend URL in production** |
| `PORT` | optional | Default 8000 (backend) / 7860 (single image) |
| `APP_ENV` | optional | `development` / `production` |

### 4.4 Rate limits and the model chain

Since the Gemini free tier is prone to `429`/quota errors, `crew_specialists.py`:

1. **Orders `MODEL_CHAIN`** to prefer fast, reliable providers (Groq → OpenRouter) before Gemini.
2. **Cooldowns providers** for 60s after a rate-limit/quota error (`_PROVIDER_COOLDOWN`), skipping them on subsequent attempts in the same process.

A provider key that is unset is skipped automatically. No changes are needed to enable this.

---

## 5. Verification checklist

After any deployment:

```bash
# Backend health
curl -s <DEPLOYED_BASE_URL>/api/health

# A full audit (expect APPROVED/REJECTED/BLOCKED etc. depending on payload)
curl -s -X POST <DEPLOYED_BASE_URL>/api/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+9715xxxxxxxx","amount":1000,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":25.2,"longitude":55.2},"request_qod_slice":false}'
```

Run the backend test suite before pushing:

```bash
cd backend
source ../.venv/bin/activate   # or use your venv
python -m pytest tests -q
```

---

## 6. Common issues

- **CORS errors in production:** `FRONTEND_ORIGIN` defaults to `localhost:3000`. Set it to the deployed frontend origin.
- **Free-tier cold starts:** Render free instances and HF Spaces sleep; the first request after idle can take 30–60s. The API has a 35s audit timeout — cold starts can surface as `504`.
- **429 on audits:** The app already cooldowns rate-limited providers and falls back; if all providers are exhausted, the deterministic `synthesize_specialist_assessment` path returns a fallback assessment.
- **Memory not persisting:** Without a valid `QDRANT_URL`/`QDRANT_API_KEY`, mem0 falls back to an in-process store that resets on redeploy.
