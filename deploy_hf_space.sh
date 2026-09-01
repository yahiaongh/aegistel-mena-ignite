#!/usr/bin/env bash
# Deploy AegisTel to a free Hugging Face Space (CPU Basic, no credit card).
#
# Creates the Space, stages the current HEAD with a root `Dockerfile`
# (HF Spaces require the file to be named exactly `Dockerfile`; the project
# ships it as `Dockerfile.hf`), and pushes so HF builds and serves the app.
#
# Prerequisites (all free, no card):
#   1. A Hugging Face account (https://huggingface.co/join).
#   2. A read-write HF access token (Settings -> Access Tokens -> "Write").
#
# Usage:
#   HF_TOKEN=hf_xxxxx ./deploy_hf_space.sh [space-name]
#
# Then (see the printed steps) add the Space secrets and set the GitHub repo
# variable HF_SPACE_URL so .github/workflows/keep-hf-space-awake.yml keeps the
# Space awake automatically.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPACE_NAME="${1:-aegistel-mena-ignite}"
STAGING="$(mktemp -d "aegistel-hf-space.XXXXXX")"

cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

# Prefer the HF_TOKEN env var; otherwise fall back to HF_TOKEN= in the root
# .env (so `./deploy_hf_space.sh` works with zero token copy-pasting).
if [ -z "${HF_TOKEN:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  HF_TOKEN="$(sed -n 's/^HF_TOKEN=//p' "$REPO_ROOT/.env" | tail -1)"
  HF_TOKEN="${HF_TOKEN%\"}"; HF_TOKEN="${HF_TOKEN#\"}"
  HF_TOKEN="${HF_TOKEN%\'}"; HF_TOKEN="${HF_TOKEN#\'}"
fi
: "${HF_TOKEN:?Set HF_TOKEN in your root .env (HF_TOKEN=hf_xxxx) or via env var}"

command -v huggingface-cli >/dev/null 2>&1 || pip install --quiet --upgrade huggingface_hub

echo ">> Creating Space <$SPACE_NAME> with Docker SDK ..."
huggingface-cli login --token "$HF_TOKEN" >/dev/null
huggingface-cli repo create "$SPACE_NAME" \
  --type space --space_sdk docker

HF_USER="$(
  python3 - <<'PY'
from huggingface_hub import whoami
print(whoami()["name"])
PY
)"

echo ">> Staging HEAD with root Dockerfile for the Space ..."
(
  cd "$REPO_ROOT"
  git archive --format=tar HEAD | tar -x -C "$STAGING"
)
# If HEAD is not pushed yet, the deploy could lag; this is user's choice.
if [ ! -f "$STAGING/Dockerfile" ]; then
  # HF Spaces heavily prefer a non-root runtime user (UID 1000); Dockerfile.hf
  # runs as root, so inject a UID-1000 user + chown right before the final CMD.
  awk '
    /^CMD \[/ { print "RUN useradd -m -u 1000 user"; print "RUN chown -R user:user /app"; print "USER user"; print "ENV HOME=/home/user"; }
    { print }
  ' "$REPO_ROOT/Dockerfile.hf" > "$STAGING/Dockerfile"
fi
cat > "$STAGING/README.md" <<MD
---
title: AegisTel MENA Ignite
emoji: 🔐
colorFrom: blue
colorTo: red
sdk: docker
pinned: false
app_port: 7860
---

# AegisTel MENA Ignite

Anti-fraud audit console for MENA mobile money, running on a free CPU Space.

- Admin console: this page
- API docs: /docs
- Health: /api/health

Add your provider keys as Space **Secrets** (Settings -> Variables and
secrets): \`GROQ_API_KEY\`, \`GOOGLE_API_KEY\`, \`QDRANT_URL\`,
\`QDRANT_API_KEY\` (required for a full live verdict), plus optional
\`OPENROUTER_API_KEY\`, \`NOKIA_NAC_API_KEY\`, \`DEEPGRAM_API_KEY\`.
MD

echo ">> Pushing to huggingface.co/spaces/$HF_USER/$SPACE_NAME ..."
(
  cd "$STAGING"
  git init -q -b main
  git config user.email "deploy@aegistel.local"
  git config user.name "AegisTel Deploy"
  git add -A
  git commit -qm "Deploy AegisTel to HF Spaces"
  git push -f "https://$HF_USER:$HF_TOKEN@huggingface.co/spaces/$HF_USER/$SPACE_NAME" main
)

echo
echo "=============================================================="
echo " Space created! Public URL:"
echo "   https://$HF_USER-$SPACE_NAME.hf.space"
echo "=============================================================="
echo
echo "Next steps (no card needed):"
echo "  1. Open the Space and add Secrets (Settings -> Variables and secrets):"
echo "     GROQ_API_KEY, GOOGLE_API_KEY, QDRANT_URL, QDRANT_API_KEY"
echo "     (optional: OPENROUTER_API_KEY, NOKIA_NAC_API_KEY, DEEPGRAM_API_KEY)"
echo "     A redeploy triggers automatically once a secret changes."
echo "  2. Set the GitHub repo VARIABLE HF_SPACE_URL (Settings -> Secrets and"
echo "     variables -> Actions -> Variables) to the URL above so the"
echo "     keep-hf-space-awake workflow pings it every 10 min and it never"
echo "     sleeps. Give that URL to any judge - it loads instantly."