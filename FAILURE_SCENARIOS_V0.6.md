# Failure Scenarios and Acceptance Tests — V0.6

Each scenario: expected behavior, why, test plan. No claim ships without a corresponding test run.

## 1. Worker dies during training (no checkpoint yet)
**Expected:** identical to V0.3's existing story -- lease expires, Recovery reclaims (unmodified), job returns to `QUEUED`/`FAILED` per retry classification, no checkpoint exists to discover, next attempt trains from scratch.
**Test:** kill the training subprocess's supervising Worker before any checkpoint interval elapses; assert Recovery reclaims; assert the next attempt's discovery finds nothing, trains from scratch.

## 2. Worker dies before first checkpoint
Same as #1 -- there is no meaningfully different case here; included for completeness of the requested list, answered identically.

## 3. Worker dies immediately after checkpoint creation (local save, before upload)
**Expected:** the checkpoint never reaches the artifact-upload step -- nothing to reconcile (no `PENDING` artifact row was ever created for it, since local save and artifact-upload-initiation are sequential, not concurrent, in the subprocess's own flow). Functionally identical to #1 from the system's perspective.
**Test:** kill the subprocess between local save and the upload call; assert no artifact/checkpoint row exists for that step; assert next attempt's discovery is unaffected.

## 4. Worker dies during checkpoint upload
**Expected:** V0.5's existing artifact-consistency machinery handles this exactly as designed (ADR 013) -- the artifact row stays `PENDING`, Reconciler (unmodified) eventually resolves it to `FAILED` (upload genuinely incomplete) or self-heals to `UPLOADED` (bytes actually made it, only the flip was lost). Either way, no `checkpoints` row exists yet (registration, ADR 016, only happens after `UPLOADED`) -- if the Reconciler later flips it to `UPLOADED`, that artifact is still not a registered checkpoint until something explicitly registers it, and nothing will (the worker that would have is dead) -- it becomes an orphan, same as any other unclaimed-but-uploaded artifact (V0.5's Reconciler orphan sweep, unmodified).
**Test:** kill the worker mid-upload (reuse V0.5's artifact-consistency test technique); assert the artifact resolves per ADR 013's existing rules; assert no `checkpoints` row is ever created for it.

## 5. Worker dies after checkpoint upload but before metadata (registration) update
**Expected:** the artifact reaches `UPLOADED` (ADR 013, unaffected), but the *checkpoint registration* (ADR 016, a separate fencing-conditioned write) never happens -- the dead worker never got to attempt it. Same orphan outcome as #4's self-heal case.
**Test:** simulate an artifact reaching `UPLOADED` with no corresponding `checkpoints` row (skip the registration step intentionally); assert discovery (ADR 015) correctly never considers this artifact (it only queries the `checkpoints` table, never raw `artifacts`), proving an un-registered `UPLOADED` artifact is inert for resume purposes.

## 6. Worker dies during final model upload
**Expected:** same as #4/#5, applied to the final `MODEL` artifact and `training_run_outputs` instead of `CHECKPOINT`/`checkpoints` -- identical mechanism, different table.
**Test:** same technique, asserting `training_run_outputs` has no row, the job is reclaimable/retriable, and a later successful attempt can still complete and register normally (no unique-constraint conflict from the failed attempt, since it never got that far).

## 7. GPU unavailable
**Expected:** GPU_WORKER_MODEL_V0.6.md's verification step fails fast, before any training time is spent; reported as a transient failure; normal retry path.
**Test:** run the training subprocess in an environment with no CUDA device (or mock `torch.cuda.is_available()` to return False); assert immediate transient-failure classification, no training attempted.

## 8. GPU has insufficient memory
**Expected:** either caught by the pre-flight check (reserved GPU count/memory doesn't match what's visible) or, if it passes pre-flight but a real CUDA OOM occurs during training, caught as a runtime exception and classified transient -- same retry path either way.
**Test:** (a) mock insufficient reported device memory, assert pre-flight rejection; (b) force an actual OOM with an oversized batch in a real small-GPU test environment (or a mocked OOM exception), assert transient classification and retry.

## 9. Scheduler reserves GPU but worker environment doesn't match
**Expected:** identical to #7/#8 -- this is precisely what the pre-flight check exists to catch (GPU_WORKER_MODEL_V0.6.md); V0.6 does not attempt to reconcile *why* (operational/deployment concern, out of scope), only to fail closed rather than proceed on a false assumption.
**Test:** same as #7/#8, framed explicitly as "reservation said N GPUs, environment has fewer" rather than "no GPU at all."

## 10. Checkpoint is corrupted
**Expected:** rule 2 of ADR 015's compatibility check (re-hash at resume time) catches this -- corrupted bytes fail the hash comparison, that checkpoint is skipped, discovery moves to the next-most-recent candidate or concludes "train from scratch."
**Test:** upload a checkpoint artifact, then corrupt the bytes in storage directly (bypassing the normal upload path, simulating bit-rot or a storage-layer issue); assert discovery skips it and correctly falls through to the next candidate or scratch.

## 11. Checkpoint hash doesn't match
Same as #10 -- the mechanism is identical whether the mismatch is due to corruption or a bug elsewhere; the test and expected behavior are the same.

## 12. Compatible checkpoint exists from a previous attempt
**Expected:** the exact intended happy path of resume -- discovery finds it, all 6 rules pass, the new attempt loads it and continues from its step.
**Test:** attempt 1 checkpoints at step 50 then is killed; attempt 2 claims, discovery finds the step-50 checkpoint, all rules pass, training resumes from step 50 (verified via the reported starting step in `training_metrics`, not merely trusted).

## 13. Multiple checkpoints exist
**Expected:** discovery selects the most recent (`ORDER BY step DESC`) compatible one -- older checkpoints exist but are not selected, not deleted, not otherwise touched.
**Test:** checkpoints at steps 50, 100, 150 exist (150 most recent); assert discovery selects exactly the step-150 one when all are compatible; assert it correctly falls back to step-100 if step-150 is deliberately made incompatible (e.g., hash-corrupted), and to step-50 if 100 is also incompatible.

## 14. Two retries race to resume the same checkpoint
**Expected:** per ADR 015, this is not actually a race -- checkpoints are immutable and read-only once `UPLOADED`/registered, so both readers (in the counterfactual case two attempts somehow ran discovery "simultaneously," which V0.3's fencing already prevents from being a real scenario) would compute the identical answer. The only real race is job-claim fencing, already proven (V0.3's tests).
**Test:** (documentation/analysis test, not a new mechanism) -- confirm via code review / a direct concurrent-read test that running discovery from two sessions concurrently against the same immutable checkpoint data returns identical results, proving no lock is needed.

## 15. Training cancelled during checkpoint creation
**Expected:** cooperative, last-checkpoint-wins (STATE_TRANSITIONS_V0.6.md) -- no new mechanism. If the in-flight checkpoint's registration is fencing-conditioned and the job is still `RUNNING` at that moment (cancellation of a `RUNNING` job is cooperative, V0.2/V0.3, doesn't immediately flip status), the checkpoint likely still registers; if the Worker chooses to terminate the subprocess upon observing `cancel_requested`, the in-flight checkpoint may be lost -- either outcome is acceptable and requires no special-case code.
**Test:** cancel a job while its subprocess is mid-checkpoint-save; assert the final state is deterministic given whichever of the two orderings actually occurred (both are valid, tested as documented, not asserted as a single "correct" outcome).

## 16. Training cancelled during final artifact creation
Same as #15, applied to the final artifact / `training_run_outputs` instead of a checkpoint.

## 17. Artifact storage unavailable
**Expected:** identical to V0.5's existing behavior (ADR 013, unmodified) -- upload fails closed, row stays `PENDING`, Reconciler resolves later. No V0.6-specific handling needed; checkpoint/final-artifact uploads are ordinary artifact uploads.
**Test:** stop MinIO during a checkpoint upload attempt; assert clean failure, no crash, eventual reconciliation once MinIO returns.

## 18. PostgreSQL unavailable
**Expected:** same fail-closed posture as every prior version -- the Worker cannot claim, heartbeat, or register anything without Postgres; the training subprocess, having no direct Postgres access at all (ADR 016), simply can't get its reports relayed and will eventually be terminated once its Worker's own heartbeat fails.
**Test:** stop Postgres mid-training; assert the Worker's heartbeat loop fails, the subprocess is terminated (ADR 014), no partial/inconsistent state persists once Postgres returns.

## 19. Worker lease expires while training is still running (not stuck, just slow)
**Expected:** this must NOT happen for a healthy, slow-but-progressing training run -- the heartbeat loop (V0.3, unmodified, running on its own thread independent of the subprocess) keeps renewing regardless of how long training takes, exactly like V0.3's "slow but healthy worker is never reclaimed" guarantee, now proven under a real (or realistically-simulated-duration) training subprocess instead of a `time.sleep()`.
**Test:** run a training subprocess for longer than `LEASE_DURATION_SECONDS` with heartbeats renewing normally; assert Recovery never reclaims it; assert it completes normally.

## 20. Stale worker returns after recovery and attempts to upload/finalize
**Expected:** ADR 016's fencing-conditioned registration rejects it -- the artifact may upload successfully (independent upload lease, ADR 013), but checkpoint/final-artifact *registration* fails to match (job no longer `RUNNING` under that worker's `attempt_number`), so it never becomes a trusted checkpoint or the training run's designated output. This is the same class of proof as V0.3's split-brain test, applied to the new registration write paths.
**Test:** claim as worker-A, let its lease expire, let Recovery reclaim (worker-B's attempt begins), then have worker-A attempt a checkpoint registration using its stale `attempt_number` -- assert it affects zero rows, assert the job's actual state (owned by worker-B) is untouched.

## Clean-room / real-hardware verification (required before v0.6.0 tag)
A full run on real GPU hardware: `docker compose up` (or equivalent single-GPU environment) with a real small base model and small dataset, LoRA/QLoRA SFT to completion, checkpoints created and registered, final model artifact registered as a `ModelVersion`, full lineage query showing the complete chain including checkpoint history and metrics. Separately, an induced worker kill mid-training demonstrating real Recovery -> retry -> resume-from-checkpoint -> completion, with actual measured step/loss numbers at each stage (never estimated).
