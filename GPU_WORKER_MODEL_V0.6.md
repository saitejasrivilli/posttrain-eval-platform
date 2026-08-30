# GPU Worker Model — V0.6

## GPU verification (before any training work)
```
1. torch.cuda.is_available() -- must be True
2. torch.cuda.device_count() -- must be >= reservation.gpu (V0.4's Reservation
   row for this attempt)
3. for each visible device: torch.cuda.get_device_properties(i).total_memory
   -- must be >= reservation.memory_mb implied per-GPU share (V0.6 keeps this
   simple: single-GPU workload, so this is just "the one visible GPU has
   enough memory," not a multi-GPU allocation problem)
```
Any failure here is treated as an immediate transient failure of this attempt (ADR 005's classification, unchanged) -- the training subprocess reports this to the Worker as a `FAILED, transient` outcome without ever having touched real training time, and the normal retry path (a different worker, hopefully with a matching environment) takes over. V0.6 does not attempt automatic environment reconciliation (e.g., waiting for a specific GPU to free up) -- that's a scheduling-policy concern, not a worker concern, and out of scope (V0.4's scheduler already handles resource admission; V0.6 only adds the worker-side sanity check that what was reserved is actually what's present).

**Required clarification: failure domain, orthogonal to retry classification.** ADR 005's `error_classification` (transient/permanent/unknown) answers "should this retry" -- it does not answer "was this the model's fault or the infrastructure's fault," and conflating the two would make training metrics misleading (a run that "failed" three times because of GPU environment mismatches looks identical, in raw retry counts, to a run whose loss actually diverged three times, unless something distinguishes them). V0.6 adds a second, orthogonal tag: `attempts.failure_domain` (`INFRASTRUCTURE` | `TRAINING`, nullable -- null for non-failures), set alongside `error_classification` whenever an attempt fails.
- **GPU verification failures (this section), CUDA OOM, any pre-training-loop environment check** -> `failure_domain='INFRASTRUCTURE'`, `error_classification='transient'` (the standard case -- a different worker/environment will likely succeed).
- **An exception raised from inside the actual training loop** (a bad batch, a NaN loss, a real bug in the training code, an unsupported config combination the model itself rejects) -> `failure_domain='TRAINING'`, classified per ADR 005 same as before (usually `permanent` if it'll reproduce identically on retry, since retrying with the *same immutable config* -- TRAINING_CONFIG_V0.6.md -- won't fix a genuine training bug).
This is a query-time distinction, not a different retry mechanism -- both domains still use the identical ADR 005 retry/backoff/DLQ machinery; `failure_domain` exists purely so a query like "show me training runs that failed for real training reasons, not because a worker had no GPU" is answerable, keeping ML-relevant failure metrics honest.

## GPU memory during training
The training subprocess does not attempt to dynamically resize its usage if memory pressure appears mid-training -- an out-of-memory error during training is a real CUDA OOM exception, caught and reported as a transient failure (same classification), same retry path. V0.6 does not implement gradient checkpointing/memory-optimization tuning as a *reliability* feature (it may be used as a normal LoRA/QLoRA training technique to fit the small workload on modest hardware, but that's a training-configuration choice, not a failure-recovery mechanism).

## Why GPU checks live in the subprocess, not the Worker or Scheduler
The Scheduler (V0.4) reserves an *abstract* resource count (aggregate pool, RESOURCE_MODEL_V0.4.md) -- it has no way to know what a specific worker's actual hardware looks like (that's exactly why V0.4 explicitly deferred per-node placement/topology awareness). The Worker process itself doesn't need CUDA visibility for its own role (claim/heartbeat/finalize) -- only the training subprocess, which actually needs the GPU, needs to verify it's really there. Putting the check in the subprocess keeps the Worker's own code GPU-agnostic (it can supervise a training subprocess on a machine it itself has no CUDA context in, if ever needed) and puts the check at the one place that actually knows what "enough" means for this specific workload.
