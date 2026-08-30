#!/usr/bin/env bash
# V0.8 smoke test: a scripted, repeatable version of the live end-to-end flow
# that was proven BY HAND for V0.6/V0.7 (see PROJECT_SCORECARD.md). Runs the
# full control-plane path against a real running docker-compose stack:
#   dataset -> dataset version -> training run -> SUCCEEDED
#   -> register model version -> evaluation-config -> evaluation -> SUCCEEDED
#   -> results/metrics -> quality gate -> /metrics scrape (non-zero).
#
# Usage: BASE_URL=http://localhost:8000 scripts/smoke_test.sh
# Exit non-zero on any failure so CI treats it as a real gate.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
POLL_TIMEOUT="${POLL_TIMEOUT:-120}"
RUN_ID="$(date +%s)-$$"

j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

req() {
  # req METHOD PATH [curl-args...]
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" "$BASE_URL$path" "$@"
}

wait_for_status() {
  # wait_for_status URL JSON_KEY EXPECTED
  local url="$1" key="$2" want="$3" start now status
  start=$(date +%s)
  while true; do
    status=$(curl -sS "$url" | j "$key")
    if [ "$status" = "$want" ]; then echo "  -> $want"; return 0; fi
    if [ "$status" = "FAILED" ] || [ "$status" = "CANCELLED" ]; then
      echo "  -> terminal-but-wrong status: $status at $url" >&2; return 1
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$POLL_TIMEOUT" ]; then
      echo "  -> TIMEOUT waiting for $want (last=$status) at $url" >&2; return 1
    fi
    sleep 2
  done
}

echo "== smoke test against $BASE_URL =="

echo "[0] health"
curl -sSf "$BASE_URL/healthz" >/dev/null
curl -sSf "$BASE_URL/readyz"  >/dev/null

echo "[1] create dataset"
DS_ID=$(req POST /v1/datasets -H 'Content-Type: application/json' \
  -d "{\"name\":\"smoke-ds-$RUN_ID\"}" | j "['id']")
echo "  dataset=$DS_ID"

echo "[2] upload dataset version (JSON examples)"
TMP_DS=$(mktemp)
cat > "$TMP_DS" <<'EOF'
{"examples":[
  {"id":"e1","input":"hello world","expected_output":"hello world"},
  {"id":"e2","input":"foo bar","expected_output":"foo bar"},
  {"id":"e3","input":"mismatch","expected_output":"totally different"}
]}
EOF
DS_VER=$(req POST "/v1/datasets/$DS_ID/versions" -F "file=@$TMP_DS;type=application/json" | j "['version_number']")
rm -f "$TMP_DS"
echo "  dataset_version=$DS_VER"

echo "[3] create training run"
TR=$(req POST /v1/training-runs -H 'Content-Type: application/json' -d "{
  \"job_type\":\"sft\",
  \"dataset_id\":\"$DS_ID\",
  \"dataset_version_number\":$DS_VER,
  \"training_config\":{\"max_steps\":6,\"checkpoint_every_n_steps\":2,\"learning_rate\":0.5,\"target_value\":1.0},
  \"code_commit\":\"smoke-commit\",
  \"container_image\":\"trainer:latest\"
}")
TR_ID=$(echo "$TR" | j "['id']")
TR_JOB=$(echo "$TR" | j "['job_id']")
echo "  training_run=$TR_ID job=$TR_JOB"

echo "[4] wait for training job SUCCEEDED"
wait_for_status "$BASE_URL/v1/jobs/$TR_JOB" "['status']" SUCCEEDED

echo "[5] fetch training output artifact"
ART_ID=$(curl -sS "$BASE_URL/v1/training-runs/$TR_ID/output" | j "['final_artifact_id']")
echo "  final_artifact=$ART_ID"

echo "[6] create model + register version from training output"
MODEL_ID=$(req POST /v1/models -H 'Content-Type: application/json' -d "{\"name\":\"smoke-model-$RUN_ID\"}" | j "['id']")
MV=$(req POST "/v1/models/$MODEL_ID/versions" -H 'Content-Type: application/json' \
  -d "{\"artifact_id\":\"$ART_ID\",\"training_run_id\":\"$TR_ID\"}" | j "['version_number']")
echo "  model=$MODEL_ID version=$MV"

echo "[7] create evaluation-config"
CFG_ID=$(req POST /v1/evaluation-configs -H 'Content-Type: application/json' -d '{
  "task_type":"text","metric_definitions":{},"batch_size":1,
  "evaluator_code_commit":"smoke-eval","container_image":"eval:latest"
}' | j "['id']")
echo "  eval_config=$CFG_ID"

echo "[8] create evaluation"
EV=$(req POST /v1/evaluations -H 'Content-Type: application/json' -d "{
  \"model_id\":\"$MODEL_ID\",\"model_version_number\":$MV,
  \"dataset_id\":\"$DS_ID\",\"dataset_version_number\":$DS_VER,
  \"evaluation_config_id\":\"$CFG_ID\"
}")
EV_ID=$(echo "$EV" | j "['id']")
echo "  evaluation=$EV_ID"

echo "[9] wait for evaluation SUCCEEDED"
wait_for_status "$BASE_URL/v1/evaluations/$EV_ID" "['status']" SUCCEEDED

echo "[10] fetch results + metrics"
RESULTS=$(curl -sS "$BASE_URL/v1/evaluations/$EV_ID/results")
echo "  results total=$(echo "$RESULTS" | j "['total']")"
METRICS=$(curl -sS "$BASE_URL/v1/evaluations/$EV_ID/metrics")
echo "  metrics=$METRICS"

echo "[11] create + evaluate quality gate"
GATE_ID=$(req POST /v1/quality-gates -H 'Content-Type: application/json' -d '{
  "name":"smoke-gate",
  "rules":{"all":[{"metric":"exact_match","operator":">=","value":0.5}]}
}' | j "['id']")
GATE_RESULT=$(req POST "/v1/evaluations/$EV_ID/quality-gates/$GATE_ID/evaluate")
GATE_STATUS=$(echo "$GATE_RESULT" | j "['status']")
echo "  gate_status=$GATE_STATUS"
if [ "$GATE_STATUS" != "PASS" ]; then
  echo "  -> expected quality gate PASS, got: $GATE_RESULT" >&2; exit 1
fi

echo "[12] scrape /metrics and assert non-zero real counts"
MET=$(curl -sS "$BASE_URL/metrics")
python3 - "$MET" <<'PYEOF'
import re, sys
body = sys.argv[1]
def val(name):
    for line in body.splitlines():
        m = re.match(rf"^{re.escape(name)}(\{{[^}}]*\}})?\s+([0-9.eE+-]+)$", line)
        if m:
            return float(m.group(2))
    return None
created = sum(
    float(l.split()[-1]) for l in body.splitlines()
    if l.startswith("jobs_created_total{")
)
completed = sum(
    float(l.split()[-1]) for l in body.splitlines()
    if l.startswith("jobs_completed_total{")
)
evals = val("evaluation_runs_total") or 0
exec_count = val("job_execution_seconds_count") or 0
print(f"  jobs_created_total(sum)={created} jobs_completed_total(sum)={completed} "
      f"evaluation_runs_total={evals} job_execution_seconds_count={exec_count}")
assert created >= 2, "expected >=2 jobs created (training + evaluation)"
assert completed >= 2, "expected >=2 jobs completed"
assert evals >= 1, "expected >=1 evaluation run"
assert exec_count >= 2, "expected >=2 observed execution durations"
print("  metrics assertions passed (real non-zero counts)")
PYEOF

echo "== SMOKE TEST PASSED =="
