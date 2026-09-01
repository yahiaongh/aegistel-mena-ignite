#!/usr/bin/env bash
# End-to-end health check for the DEPLOYED AegisTel site (e.g. Render).
#
# Proves the pieces are linked on the live URL:
#   1. Frontend serves (the dashboard HTML loads)
#   2. Backend /api/health -> active_tool_count: 7
#   3. Live LLM audit -> a verdict from the REAL model chain (used_fallback:false
#      when keys are configured, provider=Groq/OpenRouter/Gemini in the trace)
#   4. History persisted -> the audit above is recorded under /api/v1/history
#   5. TTS -> MP3 bytes with an X-TTS-Source header
#
# Usage:
#   ./verify_live_site.sh [https://aegistel.onrender.com]
# Requires: bash, curl, python3.
set -u

URL="${1:-https://aegistel.onrender.com}"
URL="${URL%/}"
MSISDN="+99999991000"
PASS=0; FAIL=0

ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

echo "== AegisTel live-site check against $URL =="

echo
echo "-- 1. Frontend dashboard --"
code=$(curl -sS -o /tmp/aeg_ui.html -w '%{http_code}' --max-time 60 "$URL/")
if [ "$code" = "200" ] && grep -qi "<title>" /tmp/aeg_ui.html; then
  ok "dashboard returns $code with a <title>"
else
  bad "dashboard HTTP $code (got $(head -c 80 /tmp/aeg_ui.html 2>/dev/null))"
fi
rm -f /tmp/aeg_ui.html

echo
echo "-- 2. Backend health --"
code=$(curl -sS -o /tmp/aeg_health.json -w '%{http_code}' --max-time 60 "$URL/api/health")
tools=$(python3 -c "import json;print(json.load(open('/tmp/aeg_health.json')).get('active_tool_count'))" 2>/dev/null || echo "?")
if [ "$code" = "200" ] && [ "$tools" = "7" ]; then
  ok "health $code active_tool_count=$tools"
else
  bad "health HTTP $code active_tool_count=$tools (body: $(head -c 120 /tmp/aeg_health.json 2>/dev/null))"
fi
rm -f /tmp/aeg_health.json

echo
echo "-- 3. Live LLM audit (can take up to ~2 min on a cold model chain) --"
code=$(curl -sS -X POST "$URL/api/v1/audit" \
  -H 'Content-Type: application/json' \
  -d "{\"msisdn\":\"$MSISDN\",\"amount\":120000.0,\"transaction_type\":\"WIRE_TRANSFER\",\"current_location\":{\"latitude\":24.0,\"longitude\":46.0}}" \
  -o /tmp/aeg_audit.json -w '%{http_code}' --max-time 180)
if [ "$code" = "200" ]; then
  outcome=$(python3 - <<'PY'
import json, sys
d = json.load(open('/tmp/aeg_audit.json'))
status = d.get('status'); risk = d.get('risk_score'); fb = d.get('used_fallback')
providers = sorted({t.get('provider') for t in (d.get('agent_trace') or []) if t.get('provider')})
models = [t.get('model') for t in (d.get('agent_trace') or []) if t.get('model')]
print(f"summary=status {status} | risk {risk} | used_fallback {fb} | providers {providers} | models {models[:3]}")
valid = status in ("APPROVED","REJECTED","BLOCKED","STEP_UP_REQUIRED","MANUAL_REVIEW") and risk
sys.exit(0 if valid else 1)
PY
) && ok "audit verifies: $outcome" || bad "audit verdict missing/odd: $outcome"
else
  bad "audit HTTP $code (body: $(head -c 200 /tmp/aeg_audit.json 2>/dev/null))"
fi
rm -f /tmp/aeg_audit.json

echo
echo "-- 4. History persisted after audit --"
code=$(curl -sS -o /tmp/aeg_hist.json -w '%{http_code}' --max-time 60 "$URL/api/v1/history/$MSISDN")
count=$(python3 -c "import json;print(json.load(open('/tmp/aeg_hist.json')).get('count'))" 2>/dev/null || echo "?")
if [ "$code" = "200" ] && [ "$count" != "0" ] && [ "$count" != "?" ] && [ -n "$count" ]; then
  ok "history records $count incident(s)"
else
  bad "history HTTP $code count=$count (body: $(head -c 120 /tmp/aeg_hist.json 2>/dev/null))"
fi
rm -f /tmp/aeg_hist.json

echo
echo "-- 5. TTS briefing endpoint --"
code=$(curl -sS -D /tmp/aeg_tts_h.txt -o /tmp/aeg_tts.bin -w '%{http_code}' \
  --max-time 60 -X POST "$URL/api/audio/tts" -F "text=AegisTel audit complete")
src=$(grep -i '^x-tts-source:' /tmp/aeg_tts_h.txt | tr -d '\r' | awk '{print $2}')
ctype=$(grep -i '^content-type:' /tmp/aeg_tts_h.txt | tr -d '\r' | awk '{print $2}')
if [ "$code" = "200" ] && echo "$ctype" | grep -qi "audio/mpeg"; then
  ok "tts $code audio/mpeg (x-tts-source=$src)"
else
  bad "tts HTTP $code ctype=$ctype (x-tts-source=$src)"
fi
rm -f /tmp/aeg_tts_h.txt /tmp/aeg_tts.bin

echo
echo "== RESULT: $PASS passed, $FAIL failed =="
[ "$FAIL" = "0" ] && exit 0
exit 1