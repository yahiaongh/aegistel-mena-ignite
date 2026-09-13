# AegisTel — Live-Site End-to-End Test (aegistel.onrender.com)

The same battery as `E2E_TEST.md`, but pointed at the **deployed** app instead of
`localhost`. These commands target the live Render service
(`aegistel.onrender.com`, single container, HTTPS-only). Every route is reached
through the frontend's server-side `/api/*` rewrite — there are no ports to
guess, and the same origin serves both UI and API.

> **Read this before running anything.** This is the *shared, production demo*
> URL. Audits, feedback, QoD sessions and history records accumulate in one
> shared memory store (local JSONL on Render's ephemeral disk **plus** the
> durable Qdrant mirror), so every run leaves its mark for later visitors and
> for the judges. That is by design — but it means:
> 1. Expect **non-zero, growing** history/feedback counts, not zeros.
> 2. Never run the destructive `clear-all` unless you own a fresh throwaway
>    deploy (the only place that command appears is the guarded §12 block,
>    skipped unless you set `LIVE_CLEAR=1`).
> 3. Free Render web services **sleep after ~15 min idle**. The first request
>    after sleep takes ~20–90 s to wake and may time out — every section below
>    starts with (or tolerates) a warm-up, and all curls carry generous
>    `--max-time`.

**Verdict caveat (same as local):** the auditor is LLM-first. Simulator
personalities return an *envelope* — never an exact status — except the clean
control `+99999991001`, which is `APPROVED`/`LOW` by design.

---

## 0. Topology and prerequisites

| Component | Location | Address |
|---|---|---|
| Frontend (Next.js standalone) | Render service `aegistel` | `https://aegistel.onrender.com/` |
| Backend (FastAPI) | same container, `127.0.0.1:8000`, proxied | `https://aegistel.onrender.com/api/*` |

```bash
BASE=https://aegistel.onrender.com
ADMIN=$(grep '^AEGISTEL_ADMIN_KEY=' /home/nullstack/aegistel-mena-ignite/.env | cut -d= -f2-)
```

- The admin token **must be the value set in the Render service env** (the same
  `AEGISTEL_ADMIN_KEY` your deployment was provisioned with). If operator
  endpoints keep returning `401` while the health check passes, the Render env
  key and the local `.env` key differ — set the local one to match.
- LLM providers, Deepgram and the Nokia NaC key are configured on Render, so
  `used_fallback: false`, real provider/model sources, `audio/mpeg` TTS and live
  tool sources are all expected (subject to the quota/cold-start notes in §14).
- **Deltas vs `E2E_TEST.md`:** nothing to start (§1 becomes a warm-up wait);
  history/feedback counts drift upward because the store is shared; latency is
  higher (Render region + cross-region Nokia/LLM calls); everything is HTTPS, so
  no port or `127.0.0.1` appears anywhere.

---

## 1. Wake-up + health + proxy smoke

```bash
BASE=https://aegistel.onrender.com
# warm-up: free instances sleep; poll until health answers 200 (up to ~5 min)
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$BASE/api/health")
  [ "$code" = "200" ] && break
  sleep 10
done
echo "health after warm-up: $code"

curl -s "$BASE/api/health"
curl -s -o /dev/null -w "frontend: %{http_code}\n" -m 60 "$BASE/"           # -> 200
curl -s -o /dev/null -w "proxy:    %{http_code}\n" -m 60 "$BASE/api/health"  # -> 200
```

Health payload: `status: ok`, `service: AegisTel`, `mode: autonomous`,
`active_tool_count: 9`, `providers_configured` true for groq/gemini/openrouter/
cerebras/deepgram. Don't assert the exact tool count — it tracks tool
registration.

## 2. Provider probe (operator)

```bash
curl -s -m 120 -H "X-Admin-Token: $ADMIN" "$BASE/api/diagnostics/provider_probe" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);[print(p['provider'],p['reachable'],p['http'],p['latency_ms']) for p in d['probes']]"
```

Every configured provider should list `reachable True` with an `http` code and a
latency. Values are a few ms higher here than on the dev box because the probe
fires from Render's egress. Anonymous probe -> `401`.

## 3. Core proof: POST /api/v1/audit

Same personalities and expected envelopes as `E2E_TEST.md` §4:

| MSISDN | Personality | Expected verdict envelope | Risk | `qod_recommended` |
|---|---|---|---|---|
| `+99999991001` | clean | **APPROVED** (exact) | LOW | false |
| `+99999991000` | fraud | REJECTED / BLOCKED / STEP_UP_REQUIRED (never APPROVED) | CRITICAL | varies |
| `+99999991002` | crowd/undocumented | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH/CRITICAL | true |
| `+99999991003` | unreachable | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH/CRITICAL | true |
| `+9999123456` | generic simulator | STEP_UP_REQUIRED (or MANUAL_REVIEW) | HIGH | true |

Verdicts are value-gated the same way as local (the $25k tripwire — confirmed
real swaps are `REJECTED`/`CRITICAL` at or above it, locked `STEP_UP_REQUIRED`
below).

```bash
BASE=https://aegistel.onrender.com
# clean control — the one exact-output case the design guarantees
curl -s -m 300 -X POST "$BASE/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991001","amount":1500.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":false}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(r['status'],r['risk_score'],r['qod_recommended'],r['used_fallback'])"
# -> APPROVED LOW False False

# fraud — assert non-approval
curl -s -m 300 -X POST "$BASE/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991000","amount":120000.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":true}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(r['status'],r['risk_score'],r['used_fallback'])"
# -> REJECTED/BLOCKED/STEP_UP_REQUIRED CRITICAL False (flips between runs)
```

Apply the same pattern to `+99999991002`, `+99999991003`, `+9999123456`: status
in `{REJECTED, BLOCKED, STEP_UP_REQUIRED, MANUAL_REVIEW}`, never `APPROVED`.
Negative schema checks (422 on invalid `transaction_type`, 422 on a smuggled
`tenant_id`, 400 on invalid E.164) are identical to the local guide.

## 4. Streaming audit (SSE)

```bash
BASE=https://aegistel.onrender.com
curl -s -N --max-time 300 -X POST "$BASE/api/v1/audit/stream" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991002","amount":5000.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":true}' \
  > /tmp/live_stream.txt
grep -c 'event: progress' /tmp/live_stream.txt   # expect >= 1
grep -c 'event: result'   /tmp/live_stream.txt   # expect 1
```

> **Remote caveat:** the proxy `proxyTimeout` is 300 s and Render passes SSE
> through, but free-tier edge buffering can make progress events arrive in
> batches. If fewer `progress` events come back than on local (where we saw 17),
> that is buffering, not a bug — the single `result` event is the hard
> assertion. If the LLM chain dies mid-stream, the frontend reruns on the
> deterministic engine (`_force_deterministic`) and the `result` event still
> lands.

## 5. Persistent audit history (operator, shared store)

History needs the admin token and is **cumulative across every visitor**, so
assert "count grew and my new audit_id is present", never an exact count:

```bash
BASE=https://aegistel.onrender.com
ADMIN=$(grep '^AEGISTEL_ADMIN_KEY=' /home/nullstack/aegistel-mena-ignite/.env | cut -d= -f2-)
before=$(curl -s -H "X-Admin-Token: $ADMIN" "$BASE/api/v1/history/+99999991001?limit=10" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['count'])")

# run one fresh clean audit, capture its audit_id from the response
AID=$(curl -s -m 300 -X POST "$BASE/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991001","amount":1500.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7}}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['audit_id'])")

curl -s -H "X-Admin-Token: $ADMIN" "$BASE/api/v1/history/+99999991001?limit=50" \
  | python3 -c "import json,sys;idx=json.load(sys.stdin);print(idx['count'], any((i.get('audit_id')==sys.argv[1]) for i in idx['incidents']))" "$AID"
# -> count (>= before+1) True

curl -s -o /dev/null -w "%{http_code}\n" "$BASE/api/v1/history/+99999991001"   # -> 401 (anonymous)
```

## 6. QoD provisioning (operator, audited + consent-gated)

Fresh QoD-recommending audit, then the explicit provision, then the duplicate
and anonymous refusals — exactly as `E2E_TEST.md` §7 but over HTTPS:

```bash
BASE=https://aegistel.onrender.com
ADMIN=$(grep '^AEGISTEL_ADMIN_KEY=' /home/nullstack/aegistel-mena-ignite/.env | cut -d= -f2-)
AID=$(curl -s -m 300 -X POST "$BASE/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991002","amount":5000.0,"transaction_type":"WIRE_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7},"request_qod_slice":true}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(r['audit_id'])")

curl -s -X POST "$BASE/api/v1/audit/qod/provision" \
  -H 'Content-Type: application/json' -H "X-Admin-Token: $ADMIN" \
  -d "$(python3 -c "import json,sys;print(json.dumps({'audit_id':sys.argv[1],'msisdn':'+99999991002','profile':'QOS_E','duration_seconds':1800}))" "$AID")"
# -> provisioned: true (+ a session; the sandbox one if Nokia is unreachable from Render)

# duplicate on the same audit -> 409
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/api/v1/audit/qod/provision" \
  -H 'Content-Type: application/json' -H "X-Admin-Token: $ADMIN" \
  -d "$(python3 -c "import json,sys;print(json.dumps({'audit_id':sys.argv[1],'msisdn':'+99999991002','profile':'QOS_E','duration_seconds':1800}))" "$AID")"   # -> 409

# anonymous provision -> 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/api/v1/audit/qod/provision" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,sys;print(json.dumps({'audit_id':sys.argv[1],'msisdn':'+99999991002','profile':'QOS_E','duration_seconds':1800}))" "$AID")"   # -> 401
```

(The `python3 -c json.dumps` wrapper keeps the nested quotes clean in shell.)

## 7. Adversarial drill

```bash
curl -s -X POST "$BASE/api/v1/drill/run" \
  -H 'Content-Type: application/json' -d '{"use_llm": false}' \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print(len(r.get('plays',[])),'plays; readiness =',r.get('readiness_score'),r.get('grade'));print('outcomes:',r.get('outcomes'))"
```

`use_llm: false` runs the deterministic variant (fast, quota-free). Observe each
play's verdict is **not approved** on the attack vectors, and the deck logs
`step_up` for social-engineering/SIM-swap plays. This route is public (used by
the dashboard's Red Team card).

## 8. Copilot (grounded Q&A)

```bash
curl -s -X POST "$BASE/api/copilot/chat" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does a STEP_UP_REQUIRED verdict mean?","enhance":true}'
```

Deterministic retrieval by default; `enhance: true` polishes with the LLM chain
and degrades to the same grounded text if providers are unavailable. Missing
`question` -> 422.

## 9. Neural TTS (Deepgram)

```bash
curl -s -X POST "$BASE/api/audio/tts" \
  -F 'text=AegisTel audit complete' -o /tmp/live.mp3 -D - | grep -i 'content-type'
```

With the Deepgram key set on Render: `content-type: audio/mpeg`
(+ `x-tts-source: deepgram`), and `file /tmp/live.mp3` reports an MPEG Layer
III stream. Without the key it fails **closed** (`503` + hint) — no silent voice
swap.

## 10. Feedback (public submit + admin readback)

Submission is public (any visitor may rate); readback needs the admin token. The
store is **shared**, so use a distinctive note and hunt for it by referral id:

```bash
BASE=https://aegistel.onrender.com
# public submit -> 201
REF=$(curl -s -X POST "$BASE/api/feedback" \
  -H 'Content-Type: application/json' \
  -d '{"ratings":{"verdict":5,"explainability":5},"mood":"😊","role":"operator","note":"live-site e2e check <INSERT-YOUR-TAG>"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

# admin readback -> your referral id present, per-feature summary present
curl -s -H "X-Admin-Token: $ADMIN" "$BASE/api/feedback?limit=100" \
  | python3 -c "import json,sys;s=json.load(sys.stdin);print(s['summary']['count'],s['summary']['per_feature']['verdict']);print(any(n.get('id')==sys.argv[1] for n in s.get('latest',[])))" "$REF"

# anonymous readback -> 401
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/api/feedback?limit=100"   # -> 401
```

Feature keys are `verdict`, `explainability`, `ui`, `voice`, `drill`,
`performance` (1–5 stars); an unknown or empty ratings map -> 422. If the
readback JSON shape differs across versions, fall back to printing
`s['summary']['count']` and grepping the referral id from the raw payload.

## 11. Memory lifecycle (non-destructive by default)

History/feedback counts are cumulative and durable (Qdrant mirror), so the
meaningful check is "a new audit is recorded immediately" (already covered in
§5) plus the anonymous-refusal of the destructive endpoint:

```bash
BASE=https://aegistel.onrender.com
# anonymous clear-all -> 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/api/memory/clear-all" \
  -H 'Content-Type: application/json' -d '{}'   # -> 401
```

> **Destructive, opt-in only.** `LIVE_CLEAR=1` truncates BOTH the shared local
> store and the Qdrant mirror — wiping history/feedback every visitor has left.
> Only run this against a throwaway deploy you own:
> ```bash
> if [ "${LIVE_CLEAR:-}" = "1" ]; then
>   curl -s -X POST "$BASE/api/memory/clear-all" \
>     -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' -d '{}'
> fi
> ```

## 12. Frontend scenarios (manual browser, deployed URL)

The full scenario set in `E2E_TEST.md` §13 applies with two substitutions:

1. Open **https://aegistel.onrender.com** instead of `http://localhost:3000`.
2. The backend is shared: Audit History counts are cumulative across visitors,
   so S6/S7 assertions read **"my new audit appears and count grew"**, not an
   exact number; S7's "restart the backend" step becomes **pass without any
   action** — cross-restart durability is already proven by §5 since the
   deployed store survives sleeps/restarts via the Qdrant mirror.

Walk S1 (golden path), S2 (fraud enforcement), S3 (QoD consent gate) end to end
against the live URL. Two live-only notes:

- **S1:** the pre-filled MSISDN is still `+99999991001` with amount default
  `1000`; expected verdict is exactly `APPROVED`/`LOW`.
- **S3:** the ops key to paste into the QoD modal is the same `AEGISTEL_ADMIN_KEY`
  (value from Render env / repo-root `.env`). Duplicate-provision on one audit
  still returns `409`.

## 13. One-shot automated battery

Save the script below as `/tmp/live_e2e.sh` and run it — it prints
`PASS`/`FAIL` per check and exits non-zero on failure. It asserts exactly the
remote-safe contract: clean control `APPROVED`/`LOW`, every other personality
non-approved, `qod_recommended` true on the step-up numbers, operator endpoints
keyed, and negative paths refused.

```bash
#!/usr/bin/env bash
# Live-site one-shot e2e against the deployed AegisTel.
#   bash /tmp/live_e2e.sh [BASE_URL]     (default https://aegistel.onrender.com)
set -u
BASE="${1:-https://aegistel.onrender.com}"
BASE="${BASE%/}"
ADMIN="${AEGISTEL_ADMIN_KEY:-$(grep '^AEGISTEL_ADMIN_KEY=' /home/nullstack/aegistel-mena-ignite/.env 2>/dev/null | cut -d= -f2-)}"
PASS=0; FAIL=0
ok(){ echo "  PASS  $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

q(){ python3 -c '
import json,sys
expr=sys.argv[1]
try:
    d=json.load(sys.stdin)
    print(eval(expr))  # noqa: S307 -- controlled assertion helper
except Exception as e:
    print("ERR:"+str(e)); sys.exit(2)
' "$@"; }

echo "== live E2E against $BASE =="

# 0 wake-up
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/api/health")
for i in $(seq 1 30); do
  [ "$code" = "200" ] && break
  sleep 10
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/api/health")
done
[ "$code" = "200" ] && ok "health warm-up 200" || bad "health $code (still cold/asleep?)"

# 1 frontend + proxy
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 "$BASE/")
[ "$code" = "200" ] && ok "frontend 200" || bad "frontend $code"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 "$BASE/api/health")
[ "$code" = "200" ] && ok "proxy /api/health 200" || bad "proxy $code"

# 2 provider probe (admin)
code=$(curl -s -m 120 -H "X-Admin-Token: $ADMIN" -o /tmp/lp.json -w '%{http_code}' --compressed \
      "$BASE/api/diagnostics/provider_probe")
reach="?"
[ "$code" = "200" ] && reach=$(q "sum(1 for p in d['probes'] if p.get('reachable'))" < /tmp/lp.json)
[ "$code" = "200" ] && [ "$reach" != "0" ] && [ "$reach" != "?" ] \
  && ok "provider probe $reach reachable" || bad "provider probe $code reachable=$reach"

# 3 audits (envelope contract; SSE stream tested separately in §3/§12)
audit(){  # msisdn amount expect_approved
  local out status risk fb
  out=$(curl -s -m 300 -X POST "$BASE/api/v1/audit" -H 'Content-Type: application/json' --compressed \
    -d "{\"msisdn\":\"$1\",\"amount\":$2,\"transaction_type\":\"WIRE_TRANSFER\",\"current_location\":{\"latitude\":24.7,\"longitude\":46.7},\"request_qod_slice\":true}")
  status=$(echo "$out" | q "d.get('status')" ); risk=$(echo "$out" | q "d.get('risk_score')")
  fb=$(echo "$out" | q "d.get('used_fallback')" )
  if [ "$3" = "1" ]; then
    [ "$status" = "APPROVED" ] && [ "$risk" = "LOW" ] && ok "$1 -> $status/$risk" || bad "$1 -> $status/$risk"
  else
    case "$status" in
      REJECTED|BLOCKED|STEP_UP_REQUIRED|MANUAL_REVIEW)
        ok "$1 -> $status (non-approval)";;
      *) bad "$1 -> $status (unexpected)";;
    esac
  fi
  [ "$fb" = "False" ] || echo "    (used_fallback=$fb — LLM keys may be down)"
}
audit "+99999991001" 1500 1
audit "+99999991000" 120000 0
audit "+99999991002" 5000 0
audit "+99999991003" 8000 0
audit "+9999123456" 8000 0

# 4 negative schema
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"+99999991001","amount":1000,"transaction_type":"PEER_TRANSFER","current_location":{"latitude":24.7,"longitude":46.7}}')
[ "$code" = "422" ] && ok "invalid tx_type 422" || bad "invalid tx_type $code"

# 5 history (admin, shared store: assert count positive)
code=$(curl -s -m 60 -H "X-Admin-Token: $ADMIN" -o /tmp/lh.json -w '%{http_code}' \
      "$BASE/api/v1/history/+99999991001?limit=10")
cnt=$( [ "$code" = "200" ] && q "d.get('count',0)" < /tmp/lh.json 2>/dev/null || echo "?" )
[ "$code" = "200" ] && [ "$cnt" != "0" ] && [ "$cnt" != "?" ] \
  && ok "history count=$cnt" || bad "history $code count=$cnt"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 "$BASE/api/v1/history/+99999991001")
[ "$code" = "401" ] && ok "history anonymous 401" || bad "history anonymous $code"

# 6 drill
code=$(curl -s -m 120 -X POST "$BASE/api/v1/drill/run" -H 'Content-Type: application/json' \
      -d '{"use_llm": false}' -o /tmp/ld.json -w '%{http_code}')
sc=$( [ "$code" = "200" ] && q "d.get('readiness_score')" < /tmp/ld.json 2>/dev/null || echo "?" )
plays=$( [ "$code" = "200" ] && q "len(d.get('plays',[]))" < /tmp/ld.json 2>/dev/null || echo "?" )
[ "$code" = "200" ] && [ "$plays" != "0" ] && [ "$plays" != "?" ] \
  && ok "drill $plays plays readiness=$sc" || bad "drill $code plays=$plays"

# 7 TTS — single -o (writes /tmp/lt.mp3), code via -w
code=$(curl -s -m 60 -X POST "$BASE/api/audio/tts" \
      -F 'text=AegisTel audit complete' -o /tmp/lt.mp3 -w '%{http_code}')
code=${code##*$'\n'}
size=$( [ "$code" = "200" ] && stat -c%s /tmp/lt.mp3 2>/dev/null || echo "0" )
[ "$code" = "200" ] && [ "$size" -gt 1000 ] && ok "tts 200 ($size bytes)" || bad "tts $code size=$size"

# 8 feedback submit + identity
ref=$(curl -s -X POST "$BASE/api/feedback" -H 'Content-Type: application/json' \
  -d '{"ratings":{"verdict":5},"mood":"😊","role":"operator","note":"live battery"}' \
  | q "d.get('id')" 2>/dev/null)
[ -n "$ref" ] && [ "$ref" != "ERR" ] && ok "feedback submitted $ref" \
  || bad "feedback submit failed ($ref)"
code=$(curl -s -m 60 -H "X-Admin-Token: $ADMIN" -o /tmp/lf.json -w '%{http_code}' "$BASE/api/feedback?limit=100")
found=$( [ "$code" = "200" ] && q "any(True for n in d.get('latest',[]) if n.get('id')=='$ref')" < /tmp/lf.json 2>/dev/null || echo "False" )
[ "$code" = "200" ] && [ "$found" = "True" ] && ok "feedback readback found $ref" \
  || bad "feedback readback $code found=$found"

# 9 destructive guard (401 anonymous)
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 -X POST "$BASE/api/memory/clear-all" \
  -H 'Content-Type: application/json' -d '{}')
[ "$code" = "401" ] && ok "clear-all anonymous 401" || bad "clear-all anonymous $code"

echo
echo "== RESULT: $PASS passed, $FAIL failed =="
[ "$FAIL" = "0" ]
```

## 14. Troubleshooting (live only)

| Symptom | Cause / fix |
|---|---|
| First request after idle times out / `000` | Free Render slept (15 min idle). Re-run — the warm-up loop in §1 handles it; give it up to ~90 s. |
| `401` on every operator call | `$ADMIN` doesn't match the Render env `AEGISTEL_ADMIN_KEY`. Update the local token and re-export, or paste the Render value. |
| Audit takes >60 s, occasionally ~90 s | The LLM chain runs inline and the crew cooldowns rate-limited providers; this is the expected envelope. Keep `--max-time 300`. |
| `used_fallback: true` on live | At least one LLM key on Render is missing/expired or all models are in cooldown after quota hits. Check `/api/diagnostics/provider_probe`. |
| Fewer SSE `progress` events than local | Free-tier edge buffering. The single `result` event is the assertion; see §4. |
| History/feedback counts much higher than your runs | Shared store — other visitors/judges accumulate records. Use §5's "grew by ≥1" and referral-id assertions, never exact counts. |
| QoD provision returns the sandbox session | Nokia host unreachable from Render at that moment; the tool rode SDK → REST → sandbox. Retry once. |
| `active_tool_count` is not 9 | Cosmetic — it counts registered tools and grows; never assert an exact value. |
| Drill hangs | `run_adversarial_drill` runs under a 60 s server-side timeout; a cold instance plus LLM plays can push close to it. Use `use_llm: false` for the smoke check. |

For the full local battery and the browser scenario details, see
[`E2E_TEST.md`](./E2E_TEST.md); the live deltas are summarized in §0.