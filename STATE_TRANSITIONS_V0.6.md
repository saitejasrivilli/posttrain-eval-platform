# State Transition Documentation — V0.6

## No new job-level state machine
V0.6 does not add, remove, or rename any `jobs.status` value (still `PENDING`/`QUEUED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED`, V0.2/V0.3, unmodified). A real training job goes through exactly the same job/attempt lifecycle a simulated job does -- `QUEUED -> RUNNING -> SUCCEEDED/FAILED/(retry)QUEUED/CANCELLED/LOST-then-reclaimed`. V0.6 only changes *what happens inside* `RUNNING` (a real subprocess instead of an instant simulated call).

## Checkpoint lifecycle (new, mirrors artifact lifecycle it wraps)
```
(training subprocess reports a checkpoint was saved locally)
        |
        v
artifact upload begins (V0.5's PENDING -> UPLOADED/FAILED, unmodified, ADR 013)
        |
        v (only if UPLOADED)
checkpoint registration attempted (fencing-conditioned, ADR 016)
        |
        +-- fencing check passes -> checkpoints row inserted (permanent,
        |     immutable -- checkpoints, once registered, are never updated
        |     or deleted, same posture as attempts/reservations history)
        |
        +-- fencing check fails -> registration silently does not happen;
              the uploaded artifact exists but is orphaned (V0.5's
              Reconciler eventually flags it, unmodified mechanism)
```
No "checkpoint status" field beyond the artifact's own status -- a `checkpoints` row existing at all implies its artifact was `UPLOADED` and the registration passed fencing at that moment (the row would simply not exist otherwise, per the conditional INSERT in ADR 016).

## Final artifact / training_run_outputs lifecycle
```
(training subprocess reports successful completion, final artifact saved)
        |
        v
artifact upload (V0.5, unmodified)
        |
        v (only if UPLOADED)
training_run_outputs registration (fencing-conditioned, ADR 016) --
  exactly one row ever, per training_run_id (unique constraint, structural
  backstop matching V0.5's unique-artifact-per-model-version pattern)
        |
        v
finalize_attempt(SUCCEEDED) (V0.3, unmodified)
```
A `TrainingRun` with a `training_run_outputs` row has definitively completed successfully at least once (immutable history, never re-registered even if a later, hypothetical re-run of the same job somehow tried -- the unique constraint prevents a second row). This is independent of whether anyone has registered that artifact as a `ModelVersion` (V0.5, unchanged -- still a separate, explicit, later step).

## Retry vs resume, restated as a state diagram (ADR 015)
```
Attempt N fails (transient) -> RUNNING -> QUEUED (V0.3, unmodified)
        |
        v
Attempt N+1 claims (V0.3, unmodified) -> RUNNING
        |
        v
Training subprocess starts -> checkpoint discovery (ADR 015)
        |
        +-- compatible checkpoint found -> load its weights, continue from
        |     its recorded step
        |
        +-- none found -> load base model, start from step 0
        |
        v (either way -- SAME training_run_id, SAME lineage chain)
training continues as attempt N+1
```
The "retry" transition (job-level) and the "resume-or-scratch" decision (training-subprocess-level, made fresh every attempt) are drawn as two separate diagrams deliberately -- they are not the same state machine, and nothing links them except that resume discovery only ever runs *because* a new attempt started (for whatever reason -- transient failure, or simply the first attempt, where discovery trivially finds nothing).

## Cancellation during checkpoint/final-artifact creation
No new cancellation mechanism -- V0.2/V0.3's cooperative, last-checkpoint-wins model applies unchanged (STATE_TRANSITIONS_V0.2.md/V0.3.md). The training subprocess's own checkpoint-interval boundaries *are* the "checkpoint" V0.2 originally meant generically -- if `cancel_requested` is observed by the Worker between subprocess reports, the Worker can choose to terminate the subprocess (same SIGTERM/SIGKILL mechanism, ADR 014) rather than waiting for natural completion; whatever was already fencing-conditionally registered before that point stands, exactly like any other last-checkpoint-wins outcome.

## Relationship to V0.1-V0.5
No V0.1-V0.5 transition is removed, renamed, or made stricter in a way that rejects something previously allowed. Every new mechanism (checkpoint/output registration) is additive and layered on top of existing fencing/artifact/lineage primitives, not a parallel system.
