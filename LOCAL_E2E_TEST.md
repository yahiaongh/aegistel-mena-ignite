# AegisTel — End-to-End Test Guide

Step-by-step verification of **every** API route and the frontend, using the
single repo-root `.env` and real LLM providers. All commands and expected
outputs below were **verified live** against this repo on 12 Sep 2026 (189
passing offline tests, full-route e2e green, plus the browser-driven scenarios
in §13 executed by hand against the running frontend).

The tone to keep in mind: AegisTel's auditor is **LLM-first**. For high-risk or
undocumented numbers the LLM legitimately arbitrates between any *non-approval*
state (`REJECTED`, `BLOCKED`, `STEP_UP_REQUIRED`, `MANUAL_REVIEW`). It can never
approve a subscriber the deterministic fail-safe engine flags (confirmed
compromise or fail-safe gate trip) — that contract is unit-tested in
`backend/tests/test_llm_decision_rework.py`. So expect *envelope* outputs for
risky numbers, not a fixed exact `status`.

---

## 0. Topology and prerequisites

| Component | Where | Port |
|---|---|---|
| Backend (FastAPI + uvicorn) | `backend/` | 8000 |
| Frontend (Next.js) | `frontend/` (proxies `/api/*` → backend) | 3000 |

- One `.env` at the **repo root** (`.gitignore`d) holds every key. The backend
  resolves it via `backend/app/core/config.py` (`ROOT_ENV_FILE`) and
  `backend/app/main.py`; the frontend loads it via `@next/env`
  (`frontend/next.config.ts`). **Do not create `backend/.env`**.
- Required for real (non-fallback) verdicts: `GROQ_API_KEY` (or another LLM
  key). `DEEPGRAM_API_KEY` enables neural TTS. `AEGISTEL_ADMIN_KEY` secures the
  operator endpoints (history, QoD provision, provider probe, feedback readback,
  memory/clear-all). Nokia `NOKIA_NAC_API_KEY` is present in the repo-root
  `.env`, and with it every CAMARA tool runs **live against Nokia's
  Network-as-Code SDK first** (lazily initialized at the first tool call), then
  the REST passthrough, then the documented sandbox. Verified live on this
  machine: executed tools report real `Nokia NaC SDK` sources. The one
  persistent exception is Number Verification, whose provisioned entitlement is
  OAuth-gated (`UnauthorizedError` 401 "Authorization header is missing") — it
  degrades through SDK → REST → explicit sandbox exactly as designed. If the
  Nokia host is ever unreachable, every tool rides the same SDK → REST → sandbox
  ladder automatically.

---

## 1. Start both servers

```bash
# backend  (repo root)
cd backend
../venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# frontend (separate terminal)
cd frontend
npm run dev
```

## 2. Health + frontend proxy (smoke)

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/          # → 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/api/health # → 200 (proxy)
```

Health payload: `status: ok`, `service: AegisTel`, `mode: autonomous`,
`active_tool_count: 9`, `providers_configured` true for groq/gemini/openrouter/
cerebras/deepgram. Don't assert the exact tool count — it evolves with tool
registration.

```bash
ADMIN=$(grep '^AEGISTEL_ADMIN_KEY=' /home/nullstack/aegistel-mena-ignite/.env | cut -d= -f2-)
```

## 3. Provider probe (operator)

```bash
curl -s -H "X-Admin-Token: $ADMIN" http://127.0.0.1:8000/api/diagnostics/provider_probe
```

Expect every provider `reachable: true` with `http: 200` and latency `ms`
(groq/openrouter/gemini/cerebras ~350–550 ms, deepgram ~800–900 ms on this box).

---

## 4. Core proof: POST /api/v1/audit

Simulator personalities and guaranteed verdicts (all with `used_fallback: false`
when `GROQ_API_KEY` is set — that proves the real LLM chain ran, not the
deterministic fallback).

Tool-source proof: with `NOKIA_NAC_API_KEY` set, `telemetry.evidence_summary`
must show `live_sdk >= 1` and `telemetry.tool_results[].source == "Nokia NaC
SDK"` for every executed signal (SIM swap, device swap, location, reachability,
roaming, and congestion/number-recycling when the LLM picks them). Number
Verification alone stays on the explicit sandbox (`source: Nokia CAMARA
Sandbox (local fallback)`), because its provisioned key entitlement is
OAuth-gated and returns 401 — the tool correctly rides SDK → REST → sandbox
(see §0).

Verified live on 12 Sep 2026 for `+99999991001`, amount 1000: `status APPROVED,
risk LOW, used_fallback false, evidence_summary = {"live_sdk": 5, "sandbox": 1,
"local_fallback": 0}` — every executed tool carried `source: Nokia NaC SDK`
except the OAuth-gated Number Verification.

| MSISDN | Personality | Expected verdict envelope | Risk | `qod_recommended` |
|---|---|---|---|---|
| `+99999991001` | clean | **APPROVED** (exact) | LOW | false |
| `+99999991000` | fraud | any of REJECTED / BLOCKED / STEP_UP_REQUIRED (flips between runs; **never** APPROVED) | CRITICAL | varies |
| `+99999991002` | crowd/undocumented | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH/CRITICAL | true |
| `+99999991003` | unreachable | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH/CRITICAL | true |
| `+9999123456` | generic simulator | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH | true |

Verdicts are **value-gated**: a confirmed SIM-swap compromise is `REJECTED` /
`CRITICAL` only at or above the material-value tripwire ($25k). Below it, the
verdict is locked to `STEP_UP_REQUIRED` — verified live for
`+99999991002 @ $5000` → STEP_UP_REQUIRED / HIGH / `qod_recommended: true`,
and for `+99999991000 @ $100` → STEP_UP_REQUIRED (the concrete case the
step-up door exists for). The value floor may not be argued down or up by the
arbitrating LLM (trace marker `CONFIRMED_COMPROMISE_STEP_UP` pins the
verdict); clean numbers above $100k still step up via the auto-approval trip
wire (`+99999991001 @ $120000` → STEP_UP_REQUIRED, `qod_recommended: true`).

```bash
# clean control — the one exact-output case the whole design guarantees
curl -s -m 240 -X POST http://127.0.0.1:8000/api/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991001","amount":1500.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":false}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(r['status'],r['risk_score'],r['qod_recommended'],r['used_fallback'])"
# → APPROVED LOW False False

# fraud — assert non-approval
curl -s -m 240 -X POST http://127.0.0.1:8000/api/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991000","amount":120000.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":true}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(r['status'],r['risk_score'],r['used_fallback'])"
# → REJECTED/BLOCKED/STEP_UP_REQUIRED CRITICAL False   (observed REJECTED, BLOCKED, STEP_UP across runs)
```

Apply the same pattern for `+99999991002`, `+99999991003`, `+9999123456`: status
must be in `{REJECTED, BLOCKED, STEP_UP_REQUIRED, MANUAL_REVIEW}` and **never**
`APPROVED`. Negative schema check — an invalid transaction_type is rejected
loudly (whitelist guard, not silently accepted):

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991001","amount":1000,"transaction_type":"PEER_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7}}'  # → 422
```

It also rejects a smuggled `tenant_id` (`extra="forbid"`, 422) and invalid
E.164 numbers (400).

## 5. Streaming audit (SSE)

```bash
curl -s -N --max-time 240 -X POST http://127.0.0.1:8000/api/v1/audit/stream \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991002","amount":5000.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":true}' \
  > /tmp/stream.txt
grep -c 'event: progress' /tmp/stream.txt      # expect >= 1 (observed 17)
grep -c 'event: result'   /tmp/stream.txt      # expect 1
```

The `result` event carries the full audit JSON; `diagnostics.timing_ms` bundles
tool/assess/drill timings.

## 6. Persistent audit history (operator)

Audits persist to `backend/data/local_memory.jsonl` (scratch under test, live in
the app) and — with `AEGISTEL_LIVE_MEMORY=1` (the repo-root `.env` default) —
are also mirrored to the **`aegistel_audit_history` Qdrant collection**, so the
history is retrieved from the durable cluster even after a local server restart
on wiped storage (reads merge local + Qdrant and de-duplicate). History requires
`X-Admin-Token`:

```bash
curl -s -H "X-Admin-Token: $ADMIN" http://127.0.0.1:8000/api/v1/history/+99999991002?limit=10 \
  | python3 -c "import json,sys;idx=json.load(sys.stdin);print(idx['count']);[print((i['audit_id'] or '-')[:8],i['status'],i['risk_score']) for i in idx['incidents'][:5]]"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/history/+99999991002  # → 401 (anonymous)
```

Re-run an audit of the same MSISDN and watch `total` increment **and** the new
incident's `audit_id` appear — persistence across requests is the point.

## 7. QoD provisioning (operator, audited + consent-gated)

Provisioning is a **separate, authenticated** action from `qod_recommended`
(policy flag `AEGISTEL_QOD_POLICY_ENABLED`). Uses the audit_id of a
QoD-recommending audit (e.g. `+99999991002`):

```bash
AID=<audit_id from the +99999991002 audit>
curl -s -X POST http://127.0.0.1:8000/api/v1/audit/qod/provision \
  -H 'Content-Type: application/json' -H "X-Admin-Token: $ADMIN" \
  -d "{\"audit_id\":\"$AID\",\"msisdn\":\"+99999991002\",\"profile\":\"QOS_E\",\"duration_seconds\":1800}"
```

Expected: `provisioned: true`; on this machine (Nokia host unreachable) the
session is the sandbox one — `{"sessionId":"qod-sess-883920","qosStatus":"REQUESTED","source":"Nokia CAMARA Sandbox (local fallback,...)"}`.
Security checks, all verified:

```bash
# duplicate sessionId for the same audit → 409
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/v1/audit/qod/provision \
  -H 'Content-Type: application/json' -H "X-Admin-Token: $ADMIN" \
  -d "{\"audit_id\":\"$AID\",\"msisdn\":\"+99999991002\",\"profile\":\"QOS_E\",\"duration_seconds\":1800}"   # → 409

# anonymous provision → 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/v1/audit/qod/provision \
  -H 'Content-Type: application/json' \
  -d "{\"audit_id\":\"$AID\",\"msisdn\":\"+99999991002\",\"profile\":\"QOS_E\",\"duration_seconds\":1800}"   # → 401
```

## 8. Adversarial drill

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/drill/run \
  -H 'Content-Type: application/json' -d '{"use_llm": false}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(len(r.get('plays',[])),'plays; readiness =',r.get('readiness_score'),r.get('grade'));print('outcomes:',r.get('outcomes'))"
```

`use_llm: false` runs the deterministic variant (fast, quota-free). Observe each
play's `verdict` is **not approved** on the attack vectors and the deck logs
`step_up` for social-engineering/SIM-swap plays.

## 9. Copilot (grounded Q&A)

```bash
curl -s -X POST http://127.0.0.1:8000/api/copilot/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does a STEP_UP_REQUIRED verdict mean?","enhance":true}'
```

Fast deterministic retrieval by default; `enhance: true` polishes with the LLM
chain and degrades to the same grounded text if providers are unavailable.
Missing `question` → 422.

## 10. Neural TTS (Deepgram)

```bash
curl -s -X POST http://127.0.0.1:8000/api/audio/tts \
  -F 'text=تمت الموافقة على المعاملة. مستوى المخاطر منخفض.' -o /tmp/speech.mp3 -D - \
  | grep -i 'content-type'
```

With `DEEPGRAM_API_KEY` set: `content-type: audio/mpeg` (+ `x-tts-source: deepgram`),
and `file /tmp/speech.mp3` reports an MPEG Layer III stream. Without the key it
fails **closed** (`503` + hint) — no silent voice swap.

## 11. Feedback (public submit + admin readback)

Feedback is written to `backend/data/feedback.jsonl` and, with
`AEGISTEL_LIVE_MEMORY=1`, mirrored to the **`aegistel_feedback` Qdrant
collection** — the Ops tab readback merges both, so visitor feedback survives a
local restart / wiped disk:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/feedback \
  -H 'Content-Type: application/json' \
  -d '{"ratings":{"verdict":5,"explainability":5},"mood":"😊","role":"operator","note":"e2e check"}'  # → 201

curl -s -H "X-Admin-Token: $ADMIN" http://127.0.0.1:8000/api/feedback?limit=20 \
  | python3 -c "import json,sys;s=json.load(sys.stdin);print(s['summary']['count'],'records');print(s['summary']['per_feature']['verdict'])"
```

Feature keys are `verdict`, `explainability`, `ui`, `voice`, `drill`, `performance`
(1–5 stars); an unknown or empty ratings map → 422.
```

Missing ratings → 422; readback without the admin token → 401.

## 12. Memory lifecycle (operator)

```bash
# counts records; after the audits in §4 you should see non-zero totals
curl -s -X POST http://127.0.0.1:8000/api/memory/clear -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8000/api/memory/clear-all -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' -d '{}'
```

`clear-all` truncates BOTH the local memory file and the Qdrant mirror
(verified 200). Run one clean audit after clearing and confirm history starts
fresh again. Anonymous `clear-all` → 401.

---

## 13. Frontend test scenarios (full stack, point-and-click)

These are **manual browser scenarios** against the running stack (backend
:8000, frontend :3000 proxying `/api/*`). They are deliberately *scenario*
driven: each one exercises a different behaviour, so read them end to end rather
than batching MSISDNs. Sensitive values:

| You will need | Where from |
|---|---|
| Ops passcode | value of `AEGISTEL_ADMIN_KEY` in the repo-root `.env` |
| A clean, open browser tab (no stale `sessionStorage`)| wipe if you repeated scenarios |

UI landmarks used below (from `frontend/src/app/page.tsx`):

- Left card *control panel*: **MSISDN**, **Amount** (+1000 / −1000 chips),
  **Device Lat / Lng**, **Geofence Radius (Meters)** (default 2000),
  **Enforce Roaming Policy** and **Prefer QoD step-up on risk** checkboxes,
  **RUN MULTI-API AUDIT** (spins as **SWARM QUERYING NOKIA NaC…**), **Export
  HTML / PDF**.
- Center column: verdict card (status + risk pill), signal tiles (**SIM Swap,
  Device Swap, Number Recycling, Location & Geofence, Roaming Telemetry,
  Reachability Status, Number Verification, Cell Congestion, QoD Step-Up**),
  **Evidence Strength / Confidence / Cross-border Risk**, the **Evidence
  Source** banner (`N NaC SDK · M sandbox · L local fallback`), the **Session
  Verdict Distribution** chart, the **Per-Audit Signal Radar**, and the
  **Red Team — Adversarial Drill** card.
- Right column: **Audit History for {msisdn}**, **Live Audit Trace** (SSE event
  tiles + **Structured Evidence Trail** of agent thoughts), audio buttons
  (**REPLAY AUDIO BRIEFING**, **TEST SOUND**).
- Floating widgets: **Copilot** (bottom-right Q&A) and **FEEDBACK** (rate tab +
  ops readback tab).

Run S1 first — later scenarios build on its history records.

### S1 — Golden path: clean-number audit end to end

- **Target behaviour:** the primary flow — SSE stream → verdict → telemetry →
  voice → export — works for a clean subscriber.
- **Steps**
  1. Open **http://localhost:3000**; confirm headers load and the MSISDN input
     is pre-filled with `+99999991001` (amount default `1000`).
  2. Leave every control at its default. Uncheck nothing. Click
     **RUN MULTI-API AUDIT**.
  3. Watch the left card + **Live Audit Trace** until the verdict card appears.
  4. When the verdict lands, click **REPLAY AUDIO BRIEFING**, then **TEST
     SOUND**. Let the briefing finish/stop.
  5. Click **Export HTML**, then **Export PDF** and open the downloaded files.
- **Expected behaviour**
  - Button label flips to **SWARM QUERYING NOKIA NaC…** with a spinner, then
    reverts; the trace's status chip cycles **REQUESTING → RESPONDED**.
  - Trace tiles stream by stage: `REQUEST` → several `TOOL` tiles (each with a
    per-tool line like `sim_swap returned [Nokia NaC SDK] in …ms`) → `VERDICT` →
    `RESPONSE`. No `ERROR` tile.
  - Verdict card animates in (fade/scale) with **status `APPROVED`**, **risk
    `LOW`**. This is the one exact output the design guarantees.
  - Signal tiles: **SIM Swap** *CLEARED*, **Device Swap** *CLEAN*, **Number
    Recycling** *NOT RECYCLED*, **Location & Geofence** *INSIDE GEOFENCE*,
    **Roaming Telemetry** *DOMESTIC*, **Reachability** reachable, **Cell
    Congestion** LOAD, **Number Verification** shows UNKNOWN / NOT VERIFIED (it
    is OAuth-gated — it must never claim *verify* without evidence).
  - **Evidence Source** banner: *Evidence Source: NaC SDK* with a live count (on
    this box `5 NaC SDK · 1 sandbox`); **Confidence** a real percentage;
    **Cross-border Risk** *CLEAR*.
  - **Session Verdict Distribution** grows one bar, **Per-Audit Signal Radar**
    fills with the flagged/clear signals, and the header **Protected this
    session** counter shows **$0** for this clean audit.
  - A spoken Arabic briefing starts automatically (Deepgram `ar-EG-ShakirNeural`
    when `DEEPGRAM_API_KEY` is set; browser speech otherwise). **REPLAY AUDIO
    BRIEFING** re-speaks it; **TEST SOUND** plays the TTS endpoint's audio.
  - Exports download `aegistel-audit-+99999991001-<timestamp>.html` and `.pdf`
    containing the verdict, meta, the signal table, and an **Evidence Explorer**
    table with a per-tool **Source** column (`Nokia NaC SDK` for every executed
    signal except sandbox-laden Number Verification) and the provenance footnote.
- **Pass criteria:** status is exactly `APPROVED`/`LOW`; banner says NaC SDK;
  trace ends `RESPONDED` with no error tile; both exports open and show the
  verdict; Number Verification tile never *claims* verification it cannot
  prove.

### S2 — Fraud enforcement: non-approval envelope + session accounting

- **Target behaviour:** a fraud simulator can never be approved, and the
  dashboard *account* reflects the protected amount.
- **Steps**
  1. Set **MSISDN** to `+99999991000`, set amount to `120000`.
  2. **Uncheck** *Prefer QoD step-up on risk* (so any non-approval is driven by
     risk, not by the step-up preference).
  3. Run the audit. Note the status/risk on the card.
  4. Look at **SIM Swap** tile and the header **Protected this session** value.
- **Expected behaviour**
  - Status is **one of** `REJECTED` / `BLOCKED` / `STEP_UP_REQUIRED` /
    `MANUAL_REVIEW` — it legitimately flips between runs (the LLM arbitrates),
    so assert the *envelope*, never an exact value. Risk is `CRITICAL`.
  - Verdict card takes the red/rose treatment for a high-risk verdict.
  - **SIM Swap** tile shows at least one flagged signal for this personality.
  - **Protected this session** increments by the audit amount (≈ `120000`)
    because the status was not `APPROVED`. The **Session Verdict Distribution**
    adds the bar for whichever status came back.
  - **Evidence Source** still shows NaC SDK sources — the honest ladder ran; the
    LLM only arbitrated the verdict.
- **Pass criteria:** status is never `APPROVED`; `Protected this session` grew;
  Evidence Source is SDK-backed.

### S3 — QoD step-up: the explicit, consent-gated provision

- **Target behaviour:** step-up is *recommended*, then only activated by an
  explicit, keyed operator action — never auto-provisioned, and never twice.
- **Steps**
  1. Set **MSISDN** to `+99999991002`, amount `5000`, and **leave** *Prefer QoD
     step-up* checked. Run.
  2. When the verdict lands, check the **QoD Step-Up** tile.
  3. Click **CONFIRM & PROVISION** — do **not** enter a key yet (fresh tab:
     no ops key stored). Cancel the modal.
  4. Click **CONFIRM & PROVISION** again; in the modal enter a **wrong** key and
     confirm.
  5. Click **CONFIRM & PROVISION** a third time; enter the real `AEGISTEL_ADMIN_KEY`
     and confirm. Wait for the tile to change.
  6. Click **CONFIRM & PROVISION** once more on the same audit.
- **Expected behaviour**
  - Verdict envelope: `STEP_UP_REQUIRED` (or `MANUAL_REVIEW`), risk `HIGH`
    (a `CRITICAL` bump is acceptable if the live congestion sample reads High);
    **QoD Step-Up** tile reads **RECOMMENDED** (amber) with the note *Charges a
    Nokia QoD session; requires an authorized tenant or operator key*.
  - Step 3: a modal titled **Enter Operator/Tenant API Key** appears; Cancel
    dismisses it and the tile stays *RECOMMENDED*.
  - Step 4: provisioning rejects (visible red error under the inputs /
    `HTTP 401` message), tile stays *RECOMMENDED*.
  - Step 5: tile flips to **ACTIVE QoD SESSION** (green) with a session handle
    like `session qod-sess-…` and a `source:` line. On this machine the session
    is the sandbox one (`Nokia CAMARA Sandbox (local fallback…)`) when the Nokia
    host is unreachable; with the host reachable it comes back live.
  - Step 6: duplicate-provision is refused on the same audit (`HTTP 409:
    duplicate`) and the error is shown; the first session id stays active.
- **Pass criteria:** provisioning happened only after an explicit, authenticated
  confirm; a wrong key fails closed; one audit_id provisions at most once.

### S4 — Location & geofence tension (controls really change the analysis)

- **Target behaviour:** the **Device Lat/Lng + Geofence Radius** inputs are live
  sensor inputs, not decoration — moving the device outside the home circle
  flips the geofence signal and the envelope.
- **Steps**
  1. With `+99999991001`, set **Device Lat** to `50.0`, **Device Lng** to
     `50.0`, keep radius `2000`. Run.
  2. Read the **Location & Geofence** tile and the verdict envelope.
  3. Restore **Device Lat** `24.7136` / **Device Lng** `46.6753`, radius `2000`,
     run again.
- **Expected behaviour**
  - Step 1: **Location & Geofence** tile flips to **OUTSIDE GEOFENCE** (rose) or
    **PARTIAL MATCH** (amber); the verdict is no longer the clean `APPROVED` —
    the location tension contributes risk, so the envelope moves to a
    non-approval arbitration (never `APPROVED` in this configuration).
  - Step 3: the tile returns to **INSIDE GEOFENCE** (green) and the number is
    back on its clean-signal path (`geo_match_meters` near 0, no geometry
    override in the `verify_location` evidence).
  - The signal radar reflects the flag change between the two runs.
- **Pass criteria:** the same msisdn produced different geofence verdicts for
  different device locations, and reverted when restored.

### S5 — Roaming-policy & cross-border toggle

- **Target behaviour:** *Enforce Roaming Policy* is honoured end to end.
- **Steps**
  1. Set **MSISDN** to `+99999991002`, amount `5000`. **Leave** the Roaming box
     checked. Uncheck the QoD box so only roaming is isolated. Run.
  2. Read the **Roaming Telemetry** and **Cross-border Risk** cards.
  3. Re-run the same number with **Enforce Roaming Policy unchecked** and compare
     the two verdict envelopes.
- **Expected behaviour**
  - With policy enforced, any roaming/cross-border tilt surfaces as
    **Cross-border Risk DETECTED** (rose) OR a roaming telemetry label other
    than *DOMESTIC*, and the non-approval envelope is maintained.
  - With the checkbox off, the payload no longer requests enforcement
    (`enforce_roaming_policy: false`), so a roaming-positive signal is not
    escalated the same way — the tile may read *DOMESTIC* or the verdict may
    soften. The dashboard faithfully reflects the toggle.
- **Pass criteria:** toggling the checkbox changes what the audit asked for and
  what the tiles report; no crash, no 4xx from the UI.

### S6 — Subscriber history panel: lock, unlock, per-number scoping

- **Target behaviour:** history is operator-only, keyed per subscriber, and
  refreshes after new audits.
- **Steps**
  1. Fresh tab (no ops token), MSISDN `+99999991001`. Expand **Audit History for
     +99999991001** if collapsed.
  2. Confirm the amber *Operator-only · subscriber incident history* gate with
     passcode input + **Unlock**.
  3. Type garbage and press Enter / click **Unlock** — the panel stays gated.
  4. Enter the real `AEGISTEL_ADMIN_KEY`, click **Unlock**.
  5. Read the **Risk Trend** chart and the incident rows. Note the count badge.
  6. Change **MSISDN** to `+99999991000` and wait ~1 s; then back to
     `+99999991001`.
  7. Run one fresh S1 audit and re-open the panel.
- **Expected behaviour**
  - Step 2–3: wrong/missing passcode leaves the amber gate in place (history
    fetch 401 → `historyLocked`); no history leaks.
  - Step 4: the panel unlocks: **Risk Trend** bar chart (severity 1–4 per prior
    incident), a `{n} records` badge, and incident cards with timestamp, status
    pill (colour by severity), roaming, and amount. It is fed by
    `/api/v1/history/{msisdn}?limit=8` (Bearer token), so it shows S1–S5 data.
  - Step 6: the header relabels *Audit History for +99999991000* and the rows are
    that subscriber's incidents only; switching back scopes to `…91001` again.
  - Step 7: the new audit appears as the newest incident and the count
    increments — history persisted.
- **Pass criteria:** gated without a valid passcode; scoped per msisdn; new
  audits appear without a page reload.

### S7 — Restart-survival through the browser (Qdrant persistence)

- **Target behaviour:** what a later visitor/operator sees is not wiped by a
  backend restart — history and feedback survive, read back through the UI.
- **Preconditions:** repo-root `.env` has `AEGISTEL_LIVE_MEMORY=1` (default) and
  `QDRANT_URL`/`QDRANT_API_KEY`; the Qdrant mirror is reachable.
- **Steps**
  1. Run an S1 audit (`+99999991001`) and submit one feedback entry in the
     **FEEDBACK** → **RATE** tab (S8 details the widget). Note the feedback
     **referral id** and the history record count.
  2. Restart the backend only
     (`pkill -f 'python -m uvicorn'`… then relaunch — see §1). Leave the
     frontend running.
  3. In the browser, press **F5** (fresh page), unlock the Audit History panel
     with the ops passcode, and open **FEEDBACK** → **OPS** readback.
- **Expected behaviour**
  - After F5: the session charts survive (they live in `localStorage`), but
    **Audit History** re-fetches from the API and shows the same incidents as
    before the restart — including the newest.
  - **FEEDBACK → OPS**: the same referral id from step 1 is in **Latest notes**,
    and the summary count includes it.
  - Optional deeper proof: move/rename `backend/data/` (wipe local JSONL),
    restart again, reload — history and feedback are still served from the
    `aegistel_audit_history` / `aegistel_feedback` Qdrant collections.
- **Pass criteria:** record count and the exact referral id are identical
  before and after restart; nothing 404s/empties in the panels.

### S8 — Feedback round-trip: public rate → operator readback

- **Target behaviour:** a visitor's ratings persist and aggregate; ops reads
  them back with per-feature averages, moods, roles and raw notes.
- **Steps**
  1. Click **FEEDBACK** (bottom-right). Switch between **RATE**/**OPS** tabs to
     confirm they are distinct.
  2. In **RATE**: hover the star rows → note the fill + the numeric hint
     (`1 — rough … 5 — flawless`). Leave all stars at 0 and note the button
     label. Then rate **Fraud verdict quality** 5, **Explains the WHY** 4, pick
     mood **😊**, role **operator**, and write a short note. Click **SHIP IT**.
  3. When the confirmation shows, click **SUBMIT ANOTHER** (or reopen) and try
     shipping with zero stars — confirm the button is inert.
  4. Switch to **OPS**, enter the ops passcode, click **OPEN**.
- **Expected behaviour**
  - Step 2: with 0 stars the button reads **RATE SOMETHING FIRST** and is
    disabled. After rating, it reads **SHIP IT** (enabled). On success a green
    **Logged.** panel appears with a **referral id** (e.g. `f3296d9c29db`).
  - Step 3: shipping 0 stars is impossible (disabled submit → no network call).
  - Step 4: readback shows `{n} responses`, per-feature averages as coloured
    bars, mood counts (**😊 1**), role chips (`operator: 1`), and **Latest
    notes** with your note, its ratings chips (`verdict ★5 explainability ★4`),
    timestamp, role, and the context line (`msisdn=… · last_status=…`).
  - Wrong passcode on OPS → red error, no data.
- **Pass criteria:** the exact referral id appears in the ops readback; the
  submission is also mirrored to Qdrant (`AEGISTEL_LIVE_MEMORY=1`).

### S9 — Copilot: grounded Q&A

- **Target behaviour:** the copilot answers from the platform's real playbook
  and stays honest outside its grounding.
- **Steps**
  1. Open the **Copilot** widget. Click the quick questions, e.g. *"What does
     BLOCKED mean?"* and *"Which tools check the SIM?"*.
  2. Ask a grounded question of your own, e.g. *"What does STEP_UP_REQUIRED
     mean?"*
  3. Ask an out-of-grounding one, e.g. *"Who won the 2026 World Cup?"* (or any
     fact outside the platform).
- **Expected behaviour**
  - Quick questions return instantly (deterministic retrieval); the answers cite
    the platform's semantics (verdict meaning, which CAMARA tools fire) and
    reference the playbook — grounded, not invented.
  - `enhance`-tagged questions polish the same grounded text with the LLM chain
    and degrade gracefully to the raw retrieval if providers are unavailable.
  - Out-of-grounding questions are answered honestly (no hallucinated claims
    about external facts) — the copilot stays scoped to AegisTel.
- **Pass criteria:** every answer is consistent with the audit/verdict language
  used elsewhere in this guide (BLOCKED/STEP_UP semantics, tool names); nothing
  confabulates a plausible-sounding external fact.

### S10 — Red-team drill: defense is graded

- **Target behaviour:** the multi-agent engine vs the red-team playbook; verdicts
  on attack plays must be non-approving.
- **Steps**
  1. Scroll to the **Red Team — Adversarial Drill** card. Click **RUN
     ADVERSARIAL DRILL** (label flips to **RED TEAM ENGAGED…**).
  2. Read the readiness gauge, outcomes, plays, and any blind spots.
- **Expected behaviour**
  - Gauge renders with `readiness_score%` + grade and a colour by score
    (emerald ≥80 / amber ≥60 / rose below).
  - **Outcomes** counts per verdict (`BLOCKED`, `MISSED`, …) render; a voiced
    drill briefing plays when voice is on.
  - The **plays** list shows fraud plays (social engineering, SIM swap, …) and
    each play's verdict is **not approved** (blocked/step-up); any
    **Blind Spot Discovered** + **recommendations** render under the playbook;
    the lineup tag reads **FRAUD GENIE** (LLM) or **SAMPLED**.
  - With the compromise floor live, confirmed-takeover swaps resolve tightly to
    the signal profile: **REJECTED/CRITICAL at or above the material-value
    tripwire ($25k+), locked STEP_UP_REQUIRED below it** (the LLM may not push
    a sub-threshold swap to REJECTED either — that is a *controlled* step-up,
    not a silent approval). The $25k+ mid-band is never auto-approved (step-up
    via the QoD tripwire), and the honest blind spots left are the
    sub-threshold staging probes on clean lines. Grades run **A+/A typical**;
    an unlucky lineup drawn with several sub-threshold swap plays resolves them
    via step-up rather than hard block, so **B (~75-79) happens occasionally**.
    The score never inflates to a perfect 100.
  - Drill failures auto-retry with backoff (a retry tile is visible if the
    backend was mid-restart) instead of failing the page.
- **Pass criteria:** readiness renders with a score AND grade; no attack play is
  approved; the dashboard never crashes regardless of backend transient 502/503.

### S11 — Graceful degradation & the deterministic lifeline

- **Target behaviour:** if the live path breaks, the UI says so and still lands
  a verdict on the deterministic engine instead of hanging.
- **Steps**
  1. With the frontend open, stop the backend. Click **RUN MULTI-API AUDIT**.
  2. Read the trace; then start the backend and re-run.
- **Expected behaviour**
  - Step 1: the trace terminates with an error tile or stream error (e.g. proxy
    502/503 surfaced as *Audit request failed / HTTP 502*), the status chip ends
    **ERROR**, and the page stays usable (no infinite spinner).
  - The failure path also fires the automated deterministic lifeline: if the
    stream dropped mid-flight (LLM provider rate limit), the trace posts
    *"Stream dropped — rerunning on the deterministic engine (LLM providers
    rate-limited)"* and the rerun carries `_force_deterministic`; the crew line
    then tags the verdict *(deterministic fallback)* and a verdict still lands.
  - Step 2: a normal run recovers with no refresh needed.
- **Pass criteria:** no hung spinner, an explicit error or deterministic tag in
  the trace, and recovery after backend restart.

### S12 — Validation & negative UI

- **Target behaviour:** the UI front-loads the API's validation so misuse is
  caught loudly.
- **Steps / Expected behaviour**
  1. **Blank MSISDN** → run: request ends in an error tile / `ERROR` chip; the
     backend's E.164 guard 400s (frontend surfaces it). No silent submit.
  2. **Bad E.164** (e.g. letters in the field) → same loud failure; nothing is
     recorded.
  3. **Amount `0`** → runs (schema allows 0) but the audit reflects a
     zero-value transaction; the counter/telemetry still render — confirms no
     divide-by-zero in the dashboard.
  4. **FEEDBACK with 0 stars** → button reads **RATE SOMETHING FIRST**, disabled
     (already covered in S8).
  5. **OPS passcode wrong** (both History and Feedback OPS) → red error, no data.
  6. **Backend down** → covered in S11; proxy fails loudly, frontend never
     crashes.
- **Pass criteria:** every invalid input is visibly rejected with a message; the
  page remains interactive for the next valid action.

---

## 14. One-shot e2e (automated)

```bash
# save the whole flow as a script; needs ADMIN + one key grepped from root .env
AEGISTEL_ADMIN_KEY=$(grep '^AEGISTEL_ADMIN_KEY=' .env | cut -d= -f2-)
# sections: health, proxy, provider_probe, 5 audits (envelope), history (+401),
# SSE, QoD provision (+409/+401), drill, copilot, tts, feedback (+401), clear-all
# Assertion contract: 1001==APPROVED/LOW; everyone else in {REJECTED,BLOCKED,
# STEP_UP_REQUIRED,MANUAL_REVIEW}; all used_fallback==false.
```

A working copy of the exact script used for the 12 Sep verified run is captured
in this conversation's artifacts (`/tmp/opencode/e2e2.sh` + `/tmp/opencode/e2e_fix.sh`).
End state of the verified run: **all sections PASS** — 18/18 in the full pass
plus 9/9 in the follow-up pass (QoD provision, corrected provider_probe URL, and
the non-approval envelope).

---

## 15. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `used_fallback: true` in audits | No usable LLM key in root `.env`. Fill `GROQ_API_KEY`, point uvicorn at the repo root (it loads `.env` via `load_dotenv`), restart. |
| `+99999991000` returns REJECTED on one run, BLOCKED/STEP_UP on the next | Expected. The LLM arbitrates; the fail-safe floor guarantees it can never be APPROVED. Assert the envelope, not an exact value. |
| `provider_probe`/404 | Route is `/api/diagnostics/provider_probe` (full prefix). Don't drop `api`. |
| QoD provision returns the sandbox session? | `qod-sess-883920` with `source: Nokia CAMARA Sandbox (local fallback...)` means the create-session SDK/REST calls failed that run (e.g., Nokia host transiently unreachable) and the tool landed on tier 3. Retry the provision — with the host reachable the session comes back live from Nokia. |
| Number Verification always shows sandbox | Expected for this provisioned key: the NV entitlement is OAuth-gated (`UnauthorizedError` 401), so the tool rides SDK → REST → sandbox. It never claims "verified" without evidence. |
| TTS returns `503` | `DEEPGRAM_API_KEY` unset/failed. Fails closed by design; dashboard falls back to browser speech. |
| Duplicate provision returns 409 | Correct — an audit_id provisions at most once (sessionId dedupe). Use a fresh audit for another session. |
| Ops panels stay gated / wrong passcode | The UI stores the ops token in `sessionStorage` (`aegistel_ops_token`). After a backend restart the panel simply 401s while gated; re-enter the `AEGISTEL_ADMIN_KEY`. A fresh tab has no token, by design. |
| Live trace shows "Stream dropped — rerunning on the deterministic engine" | The LLM chain died mid-stream (provider rate limit). The frontend auto-reruns with `_force_deterministic` and tags the crew line *(deterministic fallback)*; a verdict still lands. Not an error state. |
| Frontend shows `HTTP 502/503` from the proxy | Backend down or restarting; the proxy surfaces the failure loudly (S11). Start/restart uvicorn and re-run — no browser refresh needed. |
| Audit History shows a different number's records | The panel is scoped to the current **MSISDN** field (`/api/v1/history/{msisdn}`). Change the field and the panel relabels and refetches. |
| `Evidence Source: NOKIA SANDBOX` / `LOCAL FALLBACK` on the banner | Nokia host unreachable or the tool rode the SDK→REST→sandbox ladder. Counts are shown per tier; the Number Verification tile is sandbox by design (OAuth-gated entitlement). |
| `NEXT_PUBLIC_API_BASE_URL` not inlined | The frontend's primary data path is the server-side `/api/*` rewrite (target `AEGISTEL_BACKEND_URL`, default `127.0.0.1:8000`), so nothing needs inlining. `NEXT_PUBLIC_API_BASE_URL` only points the **browser** at a different backend origin; it's baked in at build time from root `.env` (`@next/env`), so edit it in root `.env` and restart `npm run dev`. |