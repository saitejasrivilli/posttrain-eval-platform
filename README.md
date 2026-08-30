# ML Training Infrastructure

A production-style ML execution platform built incrementally from durable
job execution to real GPU training and crash-resilient checkpoint recovery.

## V0.7 — Evaluation + Quality Gates

V0.7 adds an evaluation control plane on top of the existing pipeline:
`ModelVersion → DatasetVersion → EvaluationRun` produces a normal `Job`,
admitted by the existing Scheduler and executed by the existing Worker via
a supervised subprocess (same shape as V0.6's training executor). Every
per-example result, aggregate metric, and gate outcome is written through a
second, job-liveness-conditioned fencing layer — a stale evaluator can
never corrupt authoritative state.

```text
Dataset
   ↓
Training → ModelVersion
   ↓
EvaluationConfig
   ↓
EvaluationRun → Job → Scheduler → Worker → evaluator subprocess
   ↓
per-example results + aggregate metrics
   ↓
Quality Gate
   ├── malformed rules → ERROR (never silently PASS)
   └── exact_match 1.0 ≥ 0.9 → PASS
```

Quality gates are a pure, read-only function over already-persisted
metrics: they never recompute hidden values, never mutate a `ModelVersion`,
and never promote a model automatically — gate evaluation writes only a
`quality_gate_results` row. Promotion remains a separate, explicit,
not-yet-built act (deferred to V0.8), the same discipline V0.5 established
for explicit model registration.

**V0.7 evaluation execution is currently validated with the deterministic
CPU toy evaluator. Real GPU evaluation is not claimed.** This is distinct
from V0.6, which separately validated real CUDA training on a Tesla T4.

129/129 tests passing (103 pre-existing + 26 new). Clean-room Docker
verification passing (migrations `0001`-`0023` from an empty volume). Real
end-to-end run performed against the live Docker stack: dataset → training
→ model registration → evaluation-config → evaluation → 3/3 real
per-example results → 5 real aggregate metrics → quality gate (real
`PASS`, and separately a malformed gate correctly returned `ERROR`) → a
repeated gate evaluation confirmed idempotent (not double-inserted). See
`PROJECT_SCORECARD.md` for the full evidence table.

## V0.6 — Real GPU Training + Checkpoint Resume

V0.6 executes real LoRA SFT on a Tesla T4 using `HuggingFaceTB/SmolLM2-135M`.
Training runs in a supervised subprocess and persists checkpoints that can
be validated and resumed by a new process after failure.

### Proven failure path

```text
training
  → checkpoint-10
  → process termination (exit 137, SIGKILL)
  → checkpoint survives
  → new process
  → integrity + config validation (SHA-256)
  → optimizer / scheduler / RNG state restored
  → resume at step 10
  → complete at step 20
  → final adapter artifact
```

103/103 tests passing. Clean-room Docker verification passing. Real CUDA
validation performed on a Tesla T4 (see `V0.6_GPU_VALIDATION.md` and
[`notebooks/v0.6_real_gpu_checkpoint_resume.ipynb`](notebooks/v0.6_real_gpu_checkpoint_resume.ipynb)).

## Version history

```text
V0.1 Foundation
    ↓
V0.2 Async execution + idempotency
    ↓
V0.3 Leases + fencing + recovery
    ↓
V0.4 Resource-aware scheduling
    ↓
V0.5 Dataset/model/artifact lineage
    ↓
V0.6 Real GPU training + checkpoint/resume
    ↓
V0.7 Evaluation + quality gates
```

Every version is tagged (`v0.1.0`-`v0.7.0`); see `PROJECT_SCORECARD.md` for
the full gate-by-gate evidence backing each one.

## Architecture

```text
                         ┌─────────────────────┐
                         │        API          │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │     PostgreSQL      │
                         │ Jobs / Attempts     │
                         │ Training Runs       │
                         │ Artifacts / Lineage │
                         └──────────┬──────────┘
                                    │
                              Kafka / Outbox
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      Scheduler      │
                         │ Resource reservation│
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │       Worker        │
                         │                     │
                         │ Lease + heartbeat   │
                         │ Fencing             │
                         │ Subprocess monitor  │
                         └──────────┬──────────┘
                                    │
                             supervised process
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │    Training Subprocess       │
                    │                              │
                    │ SmolLM2-135M                 │
                    │ LoRA / SFT                   │
                    │ CUDA                         │
                    │ Checkpoint / Resume          │
                    └──────────────┬───────────────┘
                                   │
                         artifact upload
                                   │
                                   ▼
                         ┌─────────────────────┐
                         │      MinIO / S3     │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Artifact + Lineage  │
                         │ SHA-256 + metadata  │
                         └─────────────────────┘

       Recovery ───────────────► stale attempt / retry
       Reconciler ─────────────► artifact consistency
       Outbox Relay ───────────► durable event delivery
```

The Worker **supervises** training; it does not contain the training
algorithm itself. Training runs as a real OS subprocess so a hung or
crashed training process can be forcibly killed (`SIGTERM`→`SIGKILL`)
without ever taking the Worker process down with it. See `ADR/014-real-training-execution.md`.

## Failure scenario: checkpoint-10 → kill → recover → step-20

Real evidence from a real Tesla T4 run (`V0.6_GPU_VALIDATION.md`):

```text
Crash exit code:       137
Checkpoint:            checkpoint-10
Integrity:             PASS (SHA-256)
Config compatibility:  PASS (base model, dataset version, LoRA config)
Optimizer state:       PRESENT
Scheduler state:       PRESENT
RNG state:             PRESENT
Resume:                step 10
Final:                 step 20
GPU:                   Tesla T4
```

The training subprocess was **actually killed** with `SIGKILL` before
completion, not stopped gracefully. A second, independent process then
discovered the checkpoint, re-verified its hash (never trusting an
"uploaded" flag alone), verified it belonged to the same model/dataset/LoRA
config, and resumed training to completion.

The same checkpoint/resume mechanics (subprocess supervision, fencing,
checkpoint discovery, retry-vs-resume separation) are additionally proven
against this repository's own application code — real Postgres, real
subprocess, real fencing — in `tests/test_training_execution.py` and the
release-blocking split-brain fencing tier in `tests/test_checkpoint_fencing.py`.

## V0.6 evidence table

| Capability | Evidence |
|---|---|
| Real GPU training | Tesla T4 / CUDA |
| Model | `HuggingFaceTB/SmolLM2-135M` |
| Fine-tuning | LoRA (PEFT) SFT |
| Checkpointing | `checkpoint-10` (11-file manifest incl. optimizer/scheduler/RNG state) |
| Crash | subprocess killed, exit code 137 (`SIGKILL`) |
| Durable checkpoint | survived process death, confirmed on disk after crash |
| Integrity | SHA-256 verified, never existence-only |
| Config validation | base-model, dataset-version, LoRA-config compatibility all checked |
| Optimizer/scheduler/RNG restoration | verified present and restored |
| Resume | step 10 → step 20, new independent process |
| Final artifact | distinct 6-file adapter manifest (no optimizer/scheduler/RNG — that's what checkpoints are for) |
| Application-code fencing | second, job-liveness-conditioned fencing layer (ADR 016) — a stale worker's uploaded checkpoint bytes are never trusted; release-blocking tests in `tests/test_checkpoint_fencing.py` |
| Regression safety | 103/103 tests passing |
| Deployment | full clean-room `docker compose down -v && up --build`, live E2E path, live worker-crash/recovery/retry, live MinIO/Postgres/Kafka failure injection |

Full raw run output: [`notebooks/v0.6_real_gpu_checkpoint_resume.ipynb`](notebooks/v0.6_real_gpu_checkpoint_resume.ipynb).
Full narrative writeup: `V0.6_GPU_VALIDATION.md`.

## Current limitations

This platform does not claim:

- multi-GPU or distributed training (DDP/NCCL)
- Kubernetes, Ray, or Slurm scheduling
- GPU topology-aware / per-node placement
- bit-for-bit deterministic training after resume
- exactly-once execution (this platform achieves effectively-once logical
  execution via fencing + idempotency, not an exactly-once delivery guarantee)
- automatic checkpoint garbage collection or a promotion/deletion workflow
- production cloud-scale capacity or multi-tenant quotas
- real cloud object-store (S3/GCS) validation — MinIO is the local/
  production-analog implementation used here
- authentication / authorization (stubbed only)
- CUDA execution proven inside this repo's own Docker/CI environment — that
  environment has no GPU; real CUDA execution is validated separately on a
  real Tesla T4 via Colab (V0.6 training only), kept explicitly distinct
  from the CPU-only application-code tests
- real GPU-based evaluation (V0.7's evaluator is the deterministic CPU toy
  evaluator only — unlike V0.6's training, no real-GPU evaluation run exists)
- automatic model promotion on quality-gate PASS (gate evaluation is
  read-only with respect to `ModelVersion`; promotion is a separate,
  explicit, not-yet-built act — deferred to V0.8)
- LLM-as-a-judge evaluation, distributed evaluation, or a configurable
  metric-plugin system beyond exact_match/token_accuracy/latency p50/p95

See `PROJECT_SCORECARD.md` for the full deferred-scope list at every version.

## Quickstart

```bash
docker compose up --build
```

Brings up API, PostgreSQL, Kafka (Redpanda), MinIO, Worker, Scheduler,
Recovery, Reconciler, and Outbox Relay. Migrations run automatically.

```bash
curl localhost:8000/healthz
curl localhost:8000/readyz
```

See `API_CHANGES_V0.1.md` through `API_CHANGES_V0.7.md` for the full endpoint
history, and `ARCHITECTURE.md` / `ARCHITECTURE_V0.6.md` / `ARCHITECTURE_V0.7.md`
for the full design rationale.

## Design docs

Every version shipped with its own requirements, architecture, ADRs, state
transitions, DB schema changes, API changes, and failure-scenario analysis
before implementation began — see the version-suffixed `.md` files and
`ADR/` directory at the repo root. `PROJECT_SCORECARD.md` is the single
source of truth for what is actually verified at each version — no
capability is marked "Done" without a corresponding test or live-run
artifact.
