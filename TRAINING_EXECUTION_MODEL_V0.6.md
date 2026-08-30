# Training Execution Model — V0.6

Operational specification of ADR 014.

## Startup sequence (inside the training subprocess)
```
1. Receive startup context from Worker: job_id, worker_id, attempt_number,
   training_run_id, dataset_version's artifact reference, base_model
   reference (artifact or HF hub id), training_config.
2. GPU verification (GPU_WORKER_MODEL_V0.6.md) -- fail fast, closed, if
   mismatch.
3. Checkpoint discovery (ADR 015) -- determine resume-or-scratch.
4. Load dataset artifact bytes (download from MinIO via the existing V0.5
   artifact-read path).
5. Load base model weights: from a checkpoint (if resuming), from a
   registered ModelVersion's artifact (if base_model_id set and not
   resuming), or from the HF hub (first time only, cached thereafter).
6. Run the LoRA/QLoRA SFT loop.
7. At each checkpoint interval: save locally, report to Worker for upload +
   fencing-conditioned registration (ADR 016).
8. At each metric interval: report to Worker for fencing-conditioned
   recording.
9. On successful completion of all configured steps: save final artifact,
   report to Worker for upload + fencing-conditioned training_run_outputs
   registration.
10. Exit 0.
```
If terminated (SIGTERM/SIGKILL, ADR 014) at any point, none of this completes gracefully by design -- whatever was already fencing-conditionally registered (prior checkpoints) stands; whatever was in-flight is simply gone, exactly like V0.2/V0.3's "worker crash mid-execution" story for the simulated executor, just with real consequences (lost GPU-minutes, not lost simulated-instant-work).

## The Worker-subprocess communication channel
The subprocess does not talk to Postgres directly with its own credentials -- it reports events (checkpoint produced, metric recorded, final artifact produced) to its supervising Worker process over a simple local channel (a pipe, or writing structured lines the Worker reads from the subprocess's stdout -- an implementation detail, not a new distributed-systems boundary). The Worker performs every actual database write, always applying the fencing check (ADR 016) at that moment using its own current knowledge of `(job_id, worker_id, attempt_number)`. This keeps "who is allowed to write" centralized in the one place that already owns that responsibility (the Worker, which already does `finalize_attempt()`).

## Why the subprocess doesn't need its own fencing awareness
Because it never writes to Postgres itself, the subprocess cannot bypass fencing even if it wanted to (e.g., due to a bug, or because it kept running slightly past being killed) -- the Worker is the sole gatekeeper, and if the Worker itself has been fenced out (its own heartbeat failed), it stops trusting/relaying the subprocess's reports at all (ADR 014's termination step) rather than relying on the subprocess to somehow know it should stop reporting.

## Metrics captured (minimum, V0.6)
`step`, `loss`, `learning_rate` (as scheduled, may vary from the configured base if using a schedule), `timestamp`, `gpu_memory_allocated_mb` (real, read from the CUDA runtime, not estimated). Stored in `training_metrics` (DB_SCHEMA_CHANGES_V0.6.md), one row per reported interval, queryable per training run -- this is what "training metrics" means for V0.6, not a full observability/dashboarding system (that's V0.9's scope).

## Real vs simulated executor selection
`Job.job_type` (or an explicit `config.execution_mode` field, whichever proves cleaner at implementation time -- a decision left to the implementation, not the design, since it doesn't affect any correctness property) determines whether the Worker takes the V0.6 subprocess path or the V0.2-V0.5 in-process `_run_executor` path. Both paths converge on the identical `finalize_attempt()` call at the end -- V0.6 does not introduce a second finalization mechanism, only a second way of producing the outcome that gets finalized.
