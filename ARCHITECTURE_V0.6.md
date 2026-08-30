# ARCHITECTURE — V0.6 (updated HLD)

Delta on top of V0.1-V0.5. No new OS-level process is added -- V0.6 restructures the **Worker** internally (ADR 014) and adds new tables/write paths. The API, Outbox Relay, Recovery, Scheduler, and Reconciler processes are unmodified.

## Component diagram
```
                 API (unmodified -- V0.5's training-run/dataset/model/artifact endpoints)
                        |
                        v
                  PostgreSQL
   (existing V0.1-V0.5 tables, unmodified, plus:
    checkpoints, training_metrics, training_run_outputs -- 3 new tables)
                    ^
                    |
              +-----------+
              |  Worker   |  (restructured internally, ADR 014)
              |  (V0.3's  |
              |  claim/   |
              |  heartbeat/|
              |  finalize |
              |  role,    |
              |  unmodified|
              |  fencing) |
              +-----+-----+
                    |
                    v  spawns, supervises, signals
          +-------------------+
          | training subprocess|  (NEW, V0.6)
          | - GPU verification |
          | - load dataset     |
          |   (V0.5 artifact)  |
          | - load base model  |
          |   (V0.5 artifact   |
          |   or HF hub)       |
          | - LoRA/QLoRA loop  |
          | - checkpoint saves |
          | - metric reporting |
          | - final artifact   |
          +-------------------+
                    |
                    v  (via the Worker's fencing-conditioned writes, ADR 016)
          checkpoints / training_metrics / training_run_outputs
                    |
                    v
          Artifact upload (V0.5's EXISTING upload path, unmodified --
          same content-addressing, upload lease, Reconciler)
                    |
                    v
                MinIO (unmodified)
```

## Worker's restructured responsibilities (ADR 014)
```
process_job_message(job_id, worker_id):  # same entrypoint as V0.2-V0.5
  claim (V0.3, unmodified)
  |
  +-- heartbeat loop starts (V0.3, unmodified thread)
  |
  +-- IF job_type indicates a real training job (V0.6):
  |     spawn training subprocess, passing (job_id, worker_id, attempt_number,
  |       training_run metadata) as its startup context
  |     loop: poll subprocess for reported checkpoints/metrics, perform
  |       fencing-conditioned registration writes on its behalf (ADR 016)
  |     on subprocess exit: read its exit status/final report
  |   ELSE (V0.2-V0.5's simulated jobs, unchanged):
  |     call _run_executor() in-process exactly as before
  |
  +-- heartbeat_loop.abandoned? -> SIGTERM/SIGKILL the subprocess (ADR 014),
  |     discard any further reports from it, do not finalize
  |
  +-- finalize_attempt() (V0.3, unmodified) based on subprocess outcome
```
The simulated executor (`_run_executor`) is **not removed** -- V0.2-V0.5's tests and their documented behavior continue to work unmodified; V0.6 adds a second, real path selected by `job_type` (or an explicit `config.execution_mode` flag), keeping the simulated path as a fast, infrastructure-only test fixture that remains valuable (e.g. for testing fencing/scheduling logic without needing a GPU in CI).

## Data flow: DatasetVersion -> ... -> ModelVersion, with real bytes
```
DatasetVersion (V0.5, unmodified) -- artifact bytes = actual training data file
      |
      v
TrainingRun (V0.5, unmodified) -- references dataset_version_id, base_model_id,
      |                            training_config (now containing real
      |                            LoRA/QLoRA hyperparameters, TRAINING_CONFIG_V0.6.md)
      v
Job / Attempt (V0.2/V0.3, unmodified)
      |
      v
Scheduler admits (V0.4, unmodified) -- reservation for real GPU/CPU/memory
      |
      v
Worker claims (V0.3, unmodified) -> spawns training subprocess (NEW)
      |
      v
Training subprocess: loads dataset artifact bytes, loads base model
  (artifact bytes if base_model_id set, else downloads from HF hub once,
  cached), runs LoRA/QLoRA SFT loop
      |
      +-- periodic checkpoint -> artifact upload (V0.5, unmodified) ->
      |     fencing-conditioned checkpoint registration (NEW, ADR 016)
      |
      +-- periodic metric report -> training_metrics insert (NEW, fencing-
      |     conditioned same as checkpoints)
      |
      v (on successful completion)
final MODEL artifact -> upload (V0.5, unmodified) -> fencing-conditioned
  training_run_outputs insert (NEW, ADR 016) -> finalize_attempt(SUCCEEDED)
      |
      v
(separate, explicit, V0.5-unmodified step)
POST /v1/models/{id}/versions -- registers the final artifact as a ModelVersion
      |
      v
GET /v1/models/{id}/versions/{n}/lineage (V0.5, unmodified query shape,
  now also showing checkpoint history and training metrics via new joins)
```

## Config additions
`TRAINING_TERMINATION_GRACE_SECONDS` (default 10 -- SIGTERM-to-SIGKILL window, ADR 014), `CHECKPOINT_STEP_INTERVAL` (default, e.g., every N steps -- a training-config-level default, overridable per `TrainingRun`), `CHECKPOINT_FORMAT_VERSION` (current format version this codebase writes/reads, ADR 015), `BASE_MODEL_HF_CACHE_DIR` (where downloaded base models are cached locally to avoid re-downloading every attempt).

## Explicitly NOT introduced in V0.6
Multi-GPU, DDP, NCCL, Kubernetes, Ray, Slurm, model serving, inference autoscaling, checkpoint pruning/retention tooling, automatic quality gating (V0.7), a new OS-level process (training subprocess is a child of Worker, not a new service in `docker-compose.yml`'s process list beyond Worker needing GPU/CUDA access).
