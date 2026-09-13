#!/usr/bin/env bash
# Warm the Vercel function before a demo so the cold start does not land on the first caller (§3, §15).
set -euo pipefail
: "${PUBLIC_BASE_URL:?set PUBLIC_BASE_URL to the deployed origin}"
for i in 1 2 3; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "${PUBLIC_BASE_URL%/}/healthz")
  echo "warm ${i}: /healthz -> ${code}"
  sleep 1
done
