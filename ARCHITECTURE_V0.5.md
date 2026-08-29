# ARCHITECTURE — V0.5 (updated HLD)

Delta on top of V0.1-V0.4. Adds one new process (Reconciler) and one new external dependency (MinIO). Does not modify the job/attempt/lease/scheduler execution engine -- V0.5 is a metadata/lineage layer that references it.

## Component diagram
```
                 Client (curl/tests)
                        |
                        v
                 FastAPI app (extended: dataset/model/training-run routes)
                        |
                        v
                 Service / Repository
                        |
                        v
                  PostgreSQL
   (existing V0.1-V0.4 tables, unmodified, plus:
    datasets, dataset_versions, models, model_versions,
    artifacts, training_runs)
                    ^   ^
                    |   |
      +-------------+   +-------------------+
      |                                     |
+------------+                      +------------------+
| Reconciler |                      | (existing V0.1-V0.4|
| (new)      |                      |  processes,        |
| poll PENDING|                     |  unmodified: API,  |
| artifacts,  |                     |  Outbox Relay,     |
| self-heal or|                     |  Worker, Recovery, |
| fail; detect|                     |  Scheduler)        |
| orphans     |                     +------------------+
+------------+
      |
      v
   MinIO (S3-compatible object storage, new)
   -- artifact bytes only, content-addressed keys
```

## Reconciler internals
```
poll (RECONCILER_POLL_INTERVAL_MS)
  |
  +-- find `artifacts` rows with status=PENDING older than GRACE_PERIOD_SECONDS
  |     for each: check object storage for the row's storage_key
  |       exists + hash matches -> conditional UPDATE PENDING->UPLOADED (self-heal)
  |       absent -> conditional UPDATE PENDING->FAILED
  |
  +-- (secondary, lower frequency) list object storage keys, cross-check
        against `artifacts` rows -- keys with no UPLOADED/PENDING row past
        a grace period are logged as orphans (no auto-deletion, ADR 013)
```
Same crash-tolerant-by-construction design as every prior poller (Outbox Relay/Worker-heartbeat/Recovery/Scheduler): every action is one conditional check-then-update; a mid-cycle crash leaves no partial state, next poll (this instance or another) picks up where it left off.

## How a TrainingRun connects to the existing execution engine
```
POST /v1/training-runs
  |
  v
creates: training_runs row (dataset_version_id, base_model_version_id,
         training_config, code_commit, container_image, random_seed)
  +
creates: a `jobs` row via the EXISTING V0.2 job-creation path (unchanged),
         training_runs.job_id = the new job's id
  |
  v
job proceeds through the EXISTING V0.2/V0.3/V0.4 pipeline unmodified:
  QUEUED -> Scheduler admits -> Worker claims -> RUNNING -> heartbeat/lease
  -> SUCCEEDED/FAILED/retry/LOST, exactly as today
  |
  v
on SUCCEEDED: the (simulated, V0.6 will make this real) execution body
  produces bytes -> artifact creation (PENDING, ADR 013) -> upload ->
  UPLOADED, linked to training_runs.artifact_id
  |
  v
SEPARATE, EXPLICIT step: POST /v1/models/{id}/versions registers that
  artifact as a ModelVersion -- never automatic (REQUIREMENTS_V0.5.md)
```
No new job status, no new state machine for the job itself -- `TrainingRun` is a metadata wrapper that references an existing `jobs.id`, exactly the same relationship V0.4's `reservations` has to a job's attempt (a metadata table keyed by the execution engine's own identifiers, not a parallel lifecycle).

## Config additions
`MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `RECONCILER_POLL_INTERVAL_MS` (default 5000), `ARTIFACT_PENDING_GRACE_PERIOD_SECONDS` (default 300 -- for never-claimed rows), `UPLOAD_LEASE_DURATION_SECONDS` (default 60), `UPLOAD_HEARTBEAT_INTERVAL_SECONDS` (default 10 -- same margin discipline as V0.3's `LEASE_DURATION_SECONDS >= HEARTBEAT_INTERVAL_SECONDS * 3`, ADR 013).

## Explicitly NOT introduced in V0.5
Spark, Iceberg, Delta Lake, Databricks, Kubernetes, Ray, Slurm, multi-region storage, a generic lineage graph engine (ADR 012), real training execution (V0.6), evaluation/quality gates (V0.7), model promotion workflow (V0.8), artifact deletion tooling.
