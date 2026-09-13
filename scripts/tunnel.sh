#!/usr/bin/env bash
# Expose localhost:8000 to Twilio over a public wss:// URL (vr_plan.md §6.8).
#
#   1. scripts/tunnel.sh                      # prints https://xxx.trycloudflare.com and the wss:// URL
#   2. export PUBLIC_BASE_URL=https://xxx.trycloudflare.com   (BEFORE starting the app)
#   3. uv run uvicorn api.index:app --port 8000
#   Twilio console → number → "A call comes in": POST $PUBLIC_BASE_URL/api/voice/incoming
#                                "Primary handler fails": POST $PUBLIC_BASE_URL/api/voice/fallback
set -euo pipefail
PORT="${PORT:-8000}"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install: brew install cloudflared  (or use ngrok: ngrok http ${PORT})" >&2
  exit 1
fi
cloudflared tunnel --url "http://localhost:${PORT}" 2>&1 | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ (https://[a-zA-Z0-9.-]+\.trycloudflare\.com) ]]; then
    url="${BASH_REMATCH[1]}"
    echo
    echo "PUBLIC_BASE_URL=${url}"
    echo "Twilio Media Stream URL: ${url/https:/wss:}/api/voice/ws"
    echo
  fi
done
