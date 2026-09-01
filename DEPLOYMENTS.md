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
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> The backend loads keys from a `.env` file at the **repo root** (see
> `config.py`). Create it from the template — `cp backend/.env.example .env` —
> and fill in your real API keys. See `RUN_AND_TEST.md` → "Before you run —
> create your own API keys" for the mandatory/optional key table.

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

## 3. Hugging Face Spaces — **requires PRO as of 2026, not free**

> **Update (2026):** Hugging Face now returns `402 Payment Required` when
> creating *Docker* Spaces on free `cpu-basic` — only *Static* Spaces are free.
> Running AegisTel (a Docker Space) therefore needs a PRO subscription
> ($9/mo, card required). **Use Render §4 instead for free hosting.**
> `deploy_hf_space.sh` is kept only as a fallback if you later enable PRO.

The root `Dockerfile.hf` is a multi-stage build (Next.js standalone + Python
runtime) that serves both the API and the frontend from one container on the
Space's port (7860).

- **SDK:** Docker
- **Port:** 7860 (already default in `Dockerfile.hf`; the Space README sets `app_port: 7860`)
- **Build file:** Spaces require a root `Dockerfile`; `deploy_hf_space.sh`
  stages `HEAD` with the same build under the right name, plus the non-root
  UID-1000 user HF recommends.
- **Secrets:** add your provider keys in the Space settings (Settings →
  Variables and secrets); saving a secret auto-redeploys.

## 4. Render — free, no credit card, "always warm" (recommended)

The recommended option when no card is available. Render's free web-service
tier requires **no credit card**, gives **750 free instance hours/month**
(≈ 24/7 for one service), supports Docker via `Dockerfile.hf`, and serves a
public HTTPS URL (`https://aegistel.onrender.com`). The only catch — services
spin down after 15 min of inactivity (30–60s cold start) — is eliminated by the
bundled keepalive (see 4.2), so judges get an instant-loading page.

`render.yaml` (shipped at the repo root, Blueprint IaC) already declares the
web service with `runtime: docker`, `dockerfilePath: ./Dockerfile.hf`,
`plan: free`, `healthCheckPath: /api/health`, and prompts for the provider keys.

### 4.1 One-click deploy (dashboard, no CLI, no card)

1. Sign in at https://render.com with your GitHub account (no payment step).
2. **New → Blueprint** → select this repository (`yahiaongh/aegistel-mena-ignite`).
3. Render reads `render.yaml`, prompts for each `sync: false` env var — paste
   the values from your root `.env` (`GROQ_API_KEY`, `GOOGLE_API_KEY`,
   `QDRANT_URL`, `QDRANT_API_KEY`, and optionally `OPENROUTER_API_KEY`,
   `NOKIA_NAC_API_KEY`, `DEEPGRAM_API_KEY`). → Apply.
4. First build takes ~10–15 min (it builds the Next.js + Python image); then
   the service is live. Public URL: `https://aegistel.onrender.com`.

> Free-tier caveats: free web services run 0.1 vCPU / 512 MB RAM — enough for
> the demo audit path, but keep concurrent audits low. If you exceed the monthly
> bandwidth or instance hours, services are **suspended** (never billed) until
> the next reset — no surprise charges. The filesystem is ephemeral: use the
> QDRANT keys for durable audit-history/memory, which is the same persistence
> model as the demo.

### 4.2 Keep it warm (never spin down → instant load)

The bundled GitHub Action `.github/workflows/keep-aegistel-awake.yml` pings
`/api/health` every 5 minutes on public-repo free unlimited runners, comfortably
inside Render's 15-minute idle window (and it works for any deployed URL).

Enable it by setting the GitHub repo **variable** `AEGISTEL_URL` (Settings →
Secrets and variables → Actions → Variables) to your Render URL, e.g.
`https://aegistel.onrender.com`. Judges can then click the link any time with
no cold-start wait. (Optional belt-and-braces: an UptimeRobot free monitor on
the same URL checks every 5 min from outside GitHub.)

### 4.3 Blueprint (Infrastructure as Code, alternative path)

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
| `GOOGLE_API_KEY` | required for memory | Gemini; also drives QDRANT memory embeddings + extraction — needed for memory context in the verdict |
| `OPENROUTER_API_KEY` | optional | Fast reliable fallback, preferred before Gemini in `MODEL_CHAIN` |
| `CEREBRAS_API_KEY` | optional | Additional provider |
| `OPENAI_API_KEY` | optional | Additional provider |
| `DEEPGRAM_API_KEY` | optional | TTS; falls back to `edge_tts` when absent |
| `NOKIA_NAC_API_KEY` | optional (sandbox falls back) | Network-as-Code (RapidAPI) key for live CAMARA calls |
| `NOKIA_CAMARA_BASE_URL` | optional | Override NaC base URL |
| `QDRANT_URL` / `QDRANT_API_KEY` | required for memory | Persistent memory backend; feeds incident context into the verdict |
| `GEMINI_MODEL` | optional | Default `gemini-flash-latest` |
| `GROQ_MODEL` | optional | Default `openai/gpt-oss-120b` (llama-3.3-70b/llama-3.1-8b decommissioned 2026-08-16) |
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
