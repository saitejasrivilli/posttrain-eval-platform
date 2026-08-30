# ADR 016: Training Artifact Lineage — Fencing Extends to Checkpoint Writes

## Status
Proposed -- pending user review before implementation.

## Context
V0.3's fencing (ADR 004) protects `jobs` table writes (heartbeat, finalize). V0.5's artifact consistency (ADR 013) protects artifact upload/status writes via its own upload lease. V0.6 introduces a *new* write path -- registering a checkpoint (linking an uploaded artifact to a training run at a specific step) -- that sits between these two mechanisms, and it must not become a gap where a fenced-out worker can still corrupt state.

The scenario this ADR exists to prevent: a worker's job lease expires (it's partitioned, paused, or just slow) while it's mid-training. V0.3's Recovery reclaims the job, a new attempt starts. The *original* worker, unaware it's been fenced, finishes a checkpoint upload (which succeeds -- artifact upload has its own, separate lease, per ADR 013, and isn't inherently aware of the job's fencing state) and then tries to register that checkpoint against the training run, or worse, tries to upload a "final" model artifact and register it as complete. If checkpoint/final-artifact *registration* doesn't also check job-level fencing, a stale worker could inject a checkpoint or final artifact into a training run it no longer owns.

## Decision: checkpoint and final-artifact registration are fencing-conditioned on the job, in addition to the artifact's own upload-lease fencing
Two independent fencing checks compose, not one replacing the other:
1. **Artifact-level (V0.5, unchanged):** the upload itself is gated by the artifact's own upload lease (`uploader_id`, `upload_lease_expires_at`) -- this protects the artifact bytes/metadata consistency, regardless of who's uploading.
2. **Job-level (V0.6, new requirement, reuses V0.3's exact mechanism):** *registering* that artifact as a checkpoint (inserting into the `checkpoints` table) or as the final model artifact (creating the `ModelVersion`, or marking the job's designated "final artifact") is a conditional write requiring `jobs.status='RUNNING' AND jobs.lease_owner=:worker_id AND jobs.attempt_number=:my_attempt_number` -- the identical fencing predicate `finalize_attempt()` already uses (ADR 004), applied to a new table instead of `jobs` itself.
```
-- Checkpoint registration (new, V0.6):
INSERT INTO checkpoints (training_run_id, attempt_number, step, artifact_id, ...)
SELECT :training_run_id, :attempt_number, :step, :artifact_id, ...
WHERE EXISTS (
    SELECT 1 FROM jobs
    WHERE id = :job_id AND status = 'RUNNING'
      AND lease_owner = :worker_id AND attempt_number = :attempt_number
)
```
If this fencing check fails (the worker has been reclaimed since it started this checkpoint's upload), the registration simply does not happen -- rowcount 0, same "discard the result, never retry from here" rule ADR 004 established for every other fenced-out write. **The artifact itself may still exist in storage (uploaded successfully, per its own independent lease) -- it just never becomes a `checkpoint` the system considers valid**, because nothing links it to the training run. This is a harmless outcome: an unlinked, `UPLOADED`-but-never-registered artifact is functionally identical to an orphan (V0.5's Reconciler's orphan-detection sweep, unmodified, would eventually flag it for operator review if it's never referenced by anything).

## Why this must be checked at registration time, not upload time
The artifact upload itself (ADR 013) has no inherent concept of "which job/attempt this belongs to owning it at this exact moment" beyond the `job_id`/`attempt_number` tag already stored on the artifact row (V0.5's schema) -- that tag records provenance ("who produced this"), it does not re-verify current ownership at the moment of upload completion (uploads can legitimately take a while; the job's fencing state could change during that window, exactly the scenario this ADR addresses). Registration -- the moment this artifact becomes *meaningful* to the training run's lineage -- is the correct checkpoint for re-verifying fencing, because it's the last moment before the artifact starts being trusted by anything downstream (resume discovery, model registration).

## Final model artifact: the same rule, one more consequence
The final `MODEL` artifact's registration as a `ModelVersion` is *already* a separate, explicit step in V0.5 (`POST /v1/models/{id}/versions`) -- V0.6 doesn't change that API. What V0.6 adds: the training subprocess's own act of "declaring this artifact as the final output of this training run" (distinct from a caller later choosing to register it as a `ModelVersion` -- STATE_TRANSITIONS_V0.6.md) is itself fencing-conditioned exactly like checkpoint registration. A stale worker's attempt to mark an artifact as "the final output" of a training run it no longer owns fails the same way a stale checkpoint registration does.

## Distinguishing final artifacts from intermediate checkpoints (structural, not just `artifact_type`)
`artifacts.artifact_type='MODEL'` vs `'CHECKPOINT'` (V0.5, unchanged) is necessary but not sufficient by itself to answer "what is the actual final output of training run X" -- a `TrainingRun` needs exactly one designated final artifact once training completes successfully. V0.6 adds this as an explicit field: `training_runs` gains no new mutable column (immutability preserved), but a new table `training_run_outputs` (or a single row insert, fencing-conditioned same as checkpoints) records `training_run_id -> final_artifact_id` exactly once, on successful completion -- distinguishing "the" final artifact from any `CHECKPOINT`-typed rows that happen to exist, and from any `MODEL`-typed artifact that might exist for unrelated reasons (e.g. an imported base model, per V0.5's `training_run_id` being nullable on `model_versions`).

## Alternatives considered
- **Trust artifact-level upload-lease fencing alone, no job-level check at registration:** rejected -- this is exactly the gap described in Context; artifact upload leases protect *the artifact*, not *the training run's trust in that artifact*, and these are different things once a job can be reclaimed mid-upload.
- **Have the Recovery process actively clean up/invalidate a fenced-out worker's in-flight uploads:** rejected -- unnecessary complexity; the fencing-conditioned registration already makes any such stray upload inert (never linked, never trusted) without Recovery needing to know anything about artifacts at all. Keeps Recovery's responsibility exactly what it's always been (V0.3), no new cross-cutting concern added to it.

## Consequences
- Every write path introduced in V0.6 (checkpoint registration, final-artifact designation) must carry `(job_id, worker_id, attempt_number)` through from the training subprocess back to the Worker process that actually performs the fencing-conditioned write -- the subprocess itself never writes directly to Postgres with assumed authority; it reports "I produced this artifact at this step" back to its supervising Worker, which performs the fencing-conditioned registration on its behalf. This keeps a single, consistent place (the Worker) responsible for all fencing checks, exactly as it already is for `finalize_attempt()`.
