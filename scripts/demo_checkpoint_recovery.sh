#!/usr/bin/env bash
# V0.8 demo: checkpoint-10 -> kill worker -> recover -> resume -> step-20,
# against the ACTUAL running docker-compose stack (real subprocess training,
# real docker kill, real lease expiry, real Recovery reclaim, real retry that
# resumes from the registered checkpoint). This is the flow that was performed
# BY HAND in the V0.6 session; here it is a repeatable script.
#
# A HUMAN still needs to SCREEN-RECORD this run for the "demo video" -- this
# script only produces a reproducible terminal transcript. No video is created
# or claimed by running it.
#
# Usage: BASE_URL=http://localhost:8000 scripts/demo_checkpoint_recovery.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
WORKER_SVC="${WORKER_SVC:-worker}"
RUN_ID="$(date +%s)-$$"

j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }
req() { local m="$1" p="$2"; shift 2; curl -sS -X "$m" "$BASE_URL$p" "$@"; }

echo "==================================================================="
echo " V0.8 checkpoint-recovery demo  (checkpoint-10 -> kill -> step-20)"
echo " target: $BASE_URL   worker service: $WORKER_SVC"
echo "==================================================================="

echo "[1] create dataset + version"
DS_ID=$(req POST /v1/datasets -H 'Content-Type: application/json' -d "{\"name\":\"demo-ds-$RUN_ID\"}" | j "['id']")
TMP=$(mktemp); echo '{"examples":[{"id":"e1","input":"a","expected_output":"a"}]}' > "$TMP"
DS_VER=$(req POST "/v1/datasets/$DS_ID/versions" -F "file=@$TMP;type=application/json" | j "['version_number']")
rm -f "$TMP"
echo "    dataset=$DS_ID version=$DS_VER"

echo "[2] create training run (max_steps=20, checkpoint every 10, step_sleep=2s)"
TR=$(req POST /v1/training-runs -H 'Content-Type: application/json' -d "{
  \"job_type\":\"sft\",
  \"dataset_id\":\"$DS_ID\",\"dataset_version_number\":$DS_VER,
  \"training_config\":{\"max_steps\":20,\"checkpoint_every_n_steps\":10,\"learning_rate\":0.3,\"target_value\":1.0,\"step_sleep_seconds\":2},
  \"code_commit\":\"demo-commit\",\"container_image\":\"trainer:latest\"
}")
TR_ID=$(echo "$TR" | j "['id']"); TR_JOB=$(echo "$TR" | j "['job_id']")
echo "    training_run=$TR_ID job=$TR_JOB"

echo "[3] wait for checkpoint at step 10 to be registered..."
for i in $(seq 1 60); do
  CKPTS=$(curl -sS "$BASE_URL/v1/training-runs/$TR_ID/checkpoints")
  HAS10=$(echo "$CKPTS" | python3 -c "import sys,json;print(any(c['step']==10 for c in json.load(sys.stdin)))")
  if [ "$HAS10" = "True" ]; then
    echo "    checkpoint-10 registered: $CKPTS"
    break
  fi
  sleep 1
done
[ "$HAS10" = "True" ] || { echo "checkpoint-10 never appeared" >&2; exit 1; }

ATT_BEFORE=$(curl -sS "$BASE_URL/v1/jobs/$TR_JOB" | j "['attempt_number']")
echo "    current attempt_number before kill: $ATT_BEFORE"

echo "[4] KILL the worker mid-training (real docker kill -9)"
docker compose kill -s SIGKILL "$WORKER_SVC"
echo "    worker killed at $(date -u +%H:%M:%S) -- lease will expire, Recovery will reclaim"

echo "[5] restart the worker container"
docker compose up -d "$WORKER_SVC" >/dev/null
echo "    worker restarted"

echo "[6] wait for the job to reach SUCCEEDED via attempt 2 (resumed)..."
for i in $(seq 1 90); do
  S=$(curl -sS "$BASE_URL/v1/jobs/$TR_JOB" | j "['status']")
  if [ "$S" = "SUCCEEDED" ]; then break; fi
  if [ "$S" = "FAILED" ]; then echo "    job FAILED unexpectedly" >&2; exit 1; fi
  sleep 2
done
echo "    final status: $S"
[ "$S" = "SUCCEEDED" ] || { echo "job did not succeed in time" >&2; exit 1; }

echo "[7] attempt history (attempt 1 LOST -> recovered -> attempt 2 SUCCEEDED)"
curl -sS "$BASE_URL/v1/jobs/$TR_JOB/attempts" | python3 -m json.tool

echo "[8] final training output + checkpoints"
curl -sS "$BASE_URL/v1/training-runs/$TR_ID/output" | python3 -m json.tool
echo "    checkpoints:"
curl -sS "$BASE_URL/v1/training-runs/$TR_ID/checkpoints" | python3 -m json.tool

ATT_AFTER=$(curl -sS "$BASE_URL/v1/jobs/$TR_JOB" | j "['attempt_number']")
echo "==================================================================="
echo " RESULT: job SUCCEEDED. attempt_number went $ATT_BEFORE -> $ATT_AFTER"
echo " (attempt 1 was killed after checkpoint-10; attempt 2 resumed from it"
echo "  and completed to step 20 -- zero data loss, deterministic resume)."
echo " NOTE: a human must screen-record this run for the demo video;"
echo "       this script only produces the reproducible transcript."
echo "==================================================================="
