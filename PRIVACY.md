# AegisTel — Privacy notice (demo submission)

AegisTel is a fraud-decision engine for MENA payments, submitted to GSMA MENA
Ignite Hackathon 2026 under **Theme 4 — Secure Fintech, Payments & Anti-Fraud
Innovation**. This notice governs telecom data handling in the demonstration.

## 1. Purpose of telecom data

For each audited transaction AegisTel reads a minimal set of operator signals
from the CAMARA network APIs (via the Nokia Network-as-Code SDK → CAMARA REST →
documented sandbox simulator):

- SIM‑swap status
- Number‑verification (device binding)
- Device location verification (geofence)
- Roaming status
- Device reachability
- Congestion insights
- QoD slice (only after a confirmed, fresh tenant-scoped risk recommendation)

These signals are used for exactly one purpose: deciding whether the SIM and
device behind a payment really belong to the account holder, and stopping
account‑takeover (ATO) and SIM‑swap payment fraud before settlement.

## 2. Data minimization

- No message content, contact lists, call detail records, or payment
  credentials are collected.
- Only the signals listed above are read, only for the MSISDN in the audit
  request, only for the duration of that request.
- A bounded planner fetches only the signals that the transaction type and
  risk context require (see `backend/app/agents/crew_specialists.py`).
- Bounded risk metadata: at most 32 keys / 8 KB per request.

## 3. Retention

- Audit verdicts are recorded in the incident store. Default: a local JSONL
  file (`data/local_memory.jsonl`, overridable via `AEGISTEL_MEMORY_PATH`).
  When `AEGISTEL_LIVE_MEMORY=1` is set, records are mirrored to mem0/Qdrant.
- Operator history and the dashboard's audit-history panel read this store.
- Feedback submissions (`data/feedback.jsonl`) are retained for product
  iteration.

## 4. Deletion / erasure

- `POST /api/memory/clear-all` (operator-only) wipes local and remote incident
  stores, or delete the JSONL file directly.
- Feedback records are removed by deleting `data/feedback.jsonl` (or via the
  ops console before a redeploy, since the free-tier disk is ephemeral).
- No per-request telecom payloads are persisted beyond the decision record.

## 5. Operator access

History, memory-wipe, provider diagnostics, and feedback readback are
operator-only endpoints protected by `AEGISTEL_ADMIN_KEY`
(`Authorization: Bearer`, `X-Admin-Token`, or `?token=`). They fail closed
(`401` without a token; `503` when the key is unconfigured).

## 6. Synthetic-demo-data statement

The demo numbers `+99999991000`, `+99999991001`, `+99999991002`,
`+99999991003`, and `+9999123456` are synthetic Nokia NaC sandbox-simulator
subscribers. Their "incident histories" are artifacts of prior demo runs —
never real subscriber data — and simulator audits are excluded from verdict
memory-weighting. No production telephony identities are used anywhere in this
submission.
