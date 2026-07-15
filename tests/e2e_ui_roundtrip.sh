#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SIZER_E2E_PORT:-8123}"
DB="$(mktemp -t sizer-e2e-XXXXXX.sqlite3)"
LOG="$(mktemp -t sizer-e2e-XXXXXX.log)"
export PYTHONPATH="$ROOT/packages/sizer_engine:$ROOT/apps/api"
export SIZER_DB="$DB"
export SIZER_DEV_MODE=1
python -m uvicorn sizer_api.main:app --host 127.0.0.1 --port "$PORT" --log-level warning >"$LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" >/dev/null 2>&1 || true; rm -f "$DB" "$LOG"; }
trap cleanup EXIT
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/instruments/ES" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
RESPONSE="$(curl -fsS -X POST "http://127.0.0.1:$PORT/v1/size" \
  -H 'content-type: application/json' \
  --data '{"bankroll":10000,"trade_type":"prediction","payoff_structure":"binary","edge_estimate":{"win_probability":0.5},"edge_source":"guess","sample_size":120,"entry_price":0.3,"structural_max_loss":0.3,"expected_time_in_trade_days":180,"exploration_override":true}')"
python -c 'import json, sys; payload=json.load(sys.stdin); assert payload["explanation"]["binding_constraint"]; assert any("shrunk" in warning for warning in payload["diagnostics"]["warnings"]); print(payload["explanation"]["binding_constraint"])' <<<"$RESPONSE"
