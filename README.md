# ML Training Infrastructure

A production-style ML execution platform built incrementally from durable
job execution to real GPU training and crash-resilient checkpoint recovery.

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
```

Every version is tagged (`v0.1.0`-`v0.6.0`); see `PROJECT_SCORECARD.md` for
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

V0.6 does not claim:

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
  real Tesla T4 via Colab, kept explicitly distinct from the CPU-only
  application-code tests

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

See `API_CHANGES_V0.1.md` through `API_CHANGES_V0.6.md` for the full endpoint
history, and `ARCHITECTURE.md` / `ARCHITECTURE_V0.6.md` for the full design
rationale.

## Design docs

Every version shipped with its own requirements, architecture, ADRs, state
transitions, DB schema changes, API changes, and failure-scenario analysis
before implementation began — see the version-suffixed `.md` files and
`ADR/` directory at the repo root. `PROJECT_SCORECARD.md` is the single
source of truth for what is actually verified at each version — no
capability is marked "Done" without a corresponding test or live-run
artifact.
