#!/usr/bin/env bash
# End-to-end verification against a DEPLOYED Relay Worker (after worker/DEPLOY.md).
# Proves: trigger -> Action runs -> progress checkpoints land in KV -> final result;
# rate-limit blocking; and that two concurrent runs don't cross-contaminate.
#
# Usage: scripts/verify_deployed.sh https://relay-worker.<subdomain>.workers.dev
set -euo pipefail

WORKER="${1:?usage: verify_deployed.sh <worker-url>}"
SPEC="https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml"
POLL_TIMEOUT=600   # seconds to wait for the Action to finish (image build + pipeline)

command -v jq >/dev/null || { echo "jq required"; exit 1; }

create() { # -> prints runId
  curl -sS -X POST "$WORKER/api/runs" -H "Content-Type: application/json" \
    -d "{\"specUrl\":\"$SPEC\"}" | jq -r '.runId // empty'
}

poll_until_terminal() { # $1=runId -> prints final status; streams stage transitions
  local id="$1" last="" deadline=$(( $(date +%s) + POLL_TIMEOUT ))
  while (( $(date +%s) < deadline )); do
    local snap status stage
    snap=$(curl -sS "$WORKER/api/runs/$id")
    status=$(jq -r '.status' <<<"$snap"); stage=$(jq -r '.stage // "-"' <<<"$snap")
    if [[ "$status/$stage" != "$last" ]]; then echo "  [$id] $status / $stage"; last="$status/$stage"; fi
    case "$status" in succeeded|failed) echo "$status"; return 0;; esac
    sleep 3
  done
  echo "timeout"; return 1
}

echo "== 1. trigger a run =="
RUN_A=$(create); [[ -n "$RUN_A" ]] || { echo "FAIL: no runId"; exit 1; }
echo "  runId=$RUN_A"

echo "== 2. concurrent second run (isolation) =="
RUN_B=$(create)
[[ "$RUN_A" != "$RUN_B" ]] || { echo "FAIL: runIds not distinct"; exit 1; }
echo "  runId=$RUN_B (distinct: ok)"

echo "== 3. poll run A to completion =="
STATUS_A=$(poll_until_terminal "$RUN_A")
echo "== 4. poll run B to completion =="
STATUS_B=$(poll_until_terminal "$RUN_B")

echo "== 5. assert both succeeded and forecast verified_pass =="
for id in "$RUN_A" "$RUN_B"; do
  final=$(curl -sS "$WORKER/api/runs/$id")
  pass=$(jq -r '[.result.validated[]? | select(.endpoint.path=="/v1/forecast") | .status] | first // "none"' <<<"$final")
  echo "  [$id] final status=$(jq -r .status <<<"$final") forecast=$pass"
  [[ "$(jq -r .status <<<"$final")" == "succeeded" && "$pass" == "verified_pass" ]] \
    || { echo "FAIL: run $id did not reach verified_pass"; exit 1; }
done

echo "== 6. rate-limit blocks after the daily cap =="
# Two runs already consumed; fire until a 429 appears (cap default 3/IP/day).
code=0
for i in 1 2 3 4; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$WORKER/api/runs" \
    -H "Content-Type: application/json" -d "{\"specUrl\":\"$SPEC\"}")
  echo "  extra request #$i -> HTTP $code"
  [[ "$code" == "429" ]] && break
done
[[ "$code" == "429" ]] || { echo "FAIL: never got 429 (rate limit not enforced)"; exit 1; }

echo
echo "ALL CHECKS PASSED"
