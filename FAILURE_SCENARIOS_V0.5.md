# Failure Scenarios and Acceptance Tests — V0.5

Each scenario: expected behavior, why, test plan. No claim ships without a corresponding test run.

## 1. Artifact upload succeeds but metadata transaction fails
**Expected:** cannot happen for the *creation* path (metadata-first ordering, ADR 013) -- the closest real analog is the PENDING->UPLOADED status-flip failing after a successful upload. Reconciler finds bytes present at the storage key, hash matches, self-heals to `UPLOADED`.
**Test:** upload bytes directly to storage (bypassing the flip), leave the artifact row `PENDING`; run Reconciler; assert it promotes to `UPLOADED` without re-uploading.

## 2. Metadata transaction succeeds but artifact upload fails
**Expected:** row stays `PENDING`; Reconciler, after grace period (and only if no live upload lease -- ADR 013), checks storage, finds nothing, flips to `FAILED`.
**Test:** create a `PENDING` artifact row, never upload anything, never claim a lease, backdate `created_at` past the grace period, run Reconciler, assert `FAILED`.

## 2a. Upload lease races (required by design review)
**Expected (race 1 -- uploader active, Reconciler concurrent):** uploader claims the upload lease and renews it faster than the Reconciler's sweep interval; Reconciler's precondition (`upload_lease_expires_at < now()`) excludes the row entirely; upload completes normally to `UPLOADED`; Reconciler never touched it.
**Test:** simulate an uploader holding a live lease (claim, then repeatedly renew on a timer) while running Reconciler sweeps concurrently in a loop; assert the row is never marked `FAILED` while the lease is live, and reaches `UPLOADED` once the upload completes.
**Expected (race 2 -- uploader crashes before claiming or after its lease lapses):** row has no valid lease (`upload_lease_expires_at` NULL past grace period, or in the past); Reconciler correctly treats it as abandoned; checks storage, finds nothing (uploader never got far enough); marks `FAILED`.
**Test:** create a `PENDING` row, claim a lease, then simulate a crash (never renew, never complete); advance time past `upload_lease_expires_at`; run Reconciler; assert `FAILED`, and assert a second uploader could have claimed the now-expired lease before the Reconciler's action, illustrating the two are not mutually exclusive.

## 3. Training succeeds but artifact upload fails
**Expected:** the `TrainingRun`'s job shows `SUCCEEDED` (V0.2/V0.3, unaffected), but no `UPLOADED` artifact is linked -- a valid, queryable, non-error state. The training run cannot be registered as a model version until/unless a successful artifact exists.
**Test:** simulate a successful job whose artifact upload fails; assert job status is `SUCCEEDED`, training run has no `UPLOADED` artifact, attempting `POST /v1/models/{id}/versions` against the failed artifact returns 409.

## 4. Artifact exists but training run fails
**Expected:** the artifact's own status is independent of the training run's outcome -- if bytes genuinely uploaded before a later failure, the artifact remains `UPLOADED` and referenceable; whether it's ever registered is a separate human/pipeline decision.
**Test:** artifact reaches `UPLOADED`, then the owning job is separately marked `FAILED` (e.g. a post-upload step failed); assert the artifact is still queryable/registerable.

## 5. Duplicate artifact upload
**Expected:** identical content hashes to the identical storage key -- idempotent, no duplicate row (unique index on `content_hash`), no duplicate bytes.
**Test:** upload the same bytes twice (two separate requests); assert exactly one `artifacts` row exists, the second request either returns the existing row or a clear "already exists" response, never a second PENDING row racing the first.

## 6. Duplicate model registration
**Expected:** registering the same `artifact_id` as a model version twice is rejected (409) -- an artifact maps to at most one `ModelVersion` (or the second registration attempt is treated as a no-op returning the existing version, a deliberate API design choice made explicit in implementation, not left ambiguous).
**Test:** register an artifact as `ModelVersion`, attempt to register the same artifact again (same or different model); assert deterministic rejection or idempotent no-op, never a second silently-created version pointing at the same bytes under a different version number.

## 7. Duplicate dataset version registration
**Expected:** per ADR 010, re-registering identical content as a "new version" of the same dataset is *allowed* and creates a new `DatasetVersion` row (new `version_number`) sharing the existing `artifacts` row (deduped bytes) -- this is a deliberate difference from model registration (#6), documented in ADR 010.
**Test:** upload identical bytes as two separate dataset-version-creation calls against the same dataset; assert two `DatasetVersion` rows, one shared `artifacts` row.

## 8. Concurrent creation of the same dataset version
**Expected:** two concurrent requests uploading identical content each attempt to create an `artifacts` row for the same content_hash -- the unique index resolves this: one wins the insert, the other's insert fails/conflicts and must handle it (fetch-existing-and-proceed, not a 500 error) -- the eventual `DatasetVersion` creation still succeeds for both callers (each pointing at the same, now-shared, artifact).
**Test:** two threads uploading identical bytes concurrently; assert exactly one `artifacts` row, both callers' `DatasetVersion` creations succeed (potentially as two distinct versions per #7, or the second detects it's identical -- whichever behavior is implemented must be deterministic and tested, not merely "probably fine").

## 9. Two training runs reference the same dataset version
**Expected:** no special handling needed -- immutable, read-only `DatasetVersion` (DATASET_MODEL_V0.5.md). Both proceed independently with zero coordination.
**Test:** create two `TrainingRun`s against the same `dataset_version_id` concurrently; assert both succeed, no locking/blocking observed.

## 10. Dataset version modified after a training run starts
**Expected:** structurally impossible -- no update path exists for a `DatasetVersion` row (ADR 010). "Modification" always means a new version, which existing training runs simply don't reference.
**Test:** start a `TrainingRun` against dataset v1; attempt (via direct repository call, since no API exposes this) to update `dataset_versions.artifact_id`; assert no such code path exists / the attempt is rejected at the schema or service layer.

## 11. Model version references missing artifact
**Expected:** cannot happen -- the hard invariant (ARTIFACT_LIFECYCLE_V0.5.md) requires the artifact to be `UPLOADED` at registration time, re-verified in the same transaction as the `ModelVersion` insert.
**Test:** attempt to register a `PENDING` or nonexistent artifact_id as a model version; assert rejection (404/409), never a created `ModelVersion` row with a dangling/invalid reference.

## 12. Artifact is deleted while referenced
**Expected:** V0.5 has no artifact deletion endpoint at all (REQUIREMENTS_V0.5.md non-goals) -- this scenario can only occur via manual/out-of-band storage manipulation, which is explicitly an operational hazard outside this version's guarantees, not a code path to defend against.
**Test:** N/A -- documented as out of scope, not silently assumed impossible without justification.

## 13. Object storage becomes unavailable
**Expected:** upload attempts fail closed (the artifact row stays `PENDING`, or the request returns a clear error before even creating a row, depending on where in the flow the failure occurs); no job/training-run creation is blocked by object storage being down (creating a `TrainingRun` doesn't touch storage at all -- only artifact upload does, which happens later, when the job actually executes).
**Test:** stop MinIO, attempt an artifact upload; assert clean failure (row `PENDING`, eventually reconciled to `FAILED`), no crash-loop, no silent success.

## 14. PostgreSQL becomes unavailable
**Expected:** same fail-closed posture as every prior version -- Reconciler's poll cycle catches, logs, retries next cycle (same pattern as Outbox Relay/Recovery/Scheduler); no artifact operation proceeds without Postgres.
**Test:** stop Postgres mid-Reconciler-cycle; assert clean error handling, self-heals on Postgres return.

## 15. Training job is cancelled during artifact creation
**Expected:** the artifact simply follows its own lifecycle (PENDING -> eventually FAILED via reconciliation, since the cancelled job's execution body won't complete the upload) -- no special cancellation-aware artifact code (STATE_TRANSITIONS_V0.5.md).
**Test:** cancel a job mid-simulated-upload; assert the artifact reconciles to `FAILED` on schedule, the training run's job shows `CANCELLED`, no `ModelVersion` is ever creatable from it.

## 16. Training attempt is retried and produces another artifact
**Expected:** each attempt that produces bytes gets its own `artifacts` row (`attempt_number` populated) -- a retried training run's second attempt's artifact is a distinct row from the first attempt's (if the first attempt even got far enough to produce one), consistent with V0.3's per-attempt granularity.
**Test:** simulate attempt 1 producing a (subsequently irrelevant, since attempt 1 then fails for an unrelated reason) artifact, attempt 2 succeeding and producing its own artifact; assert two distinct `artifacts` rows, the `TrainingRun`'s eventual `ModelVersion` (if registered) points at attempt 2's artifact specifically.

## 17. Orphaned artifacts
**Expected:** bytes in storage with no live (`UPLOADED`/`PENDING`) metadata reference, past the grace period, are detected by the Reconciler's orphan sweep and logged for operator review -- never auto-deleted (ADR 013).
**Test:** manually place an object in storage with no corresponding `artifacts` row; run the Reconciler's orphan-detection sweep; assert it's logged/flagged, and assert the object is NOT deleted.

## 18. Partially uploaded artifacts
**Expected:** never observable as `UPLOADED` -- the status flip includes hash verification, so a partial/corrupt upload fails verification and the row stays `PENDING` (eventually `FAILED` via reconciliation), never falsely promoted.
**Test:** simulate an upload that writes fewer bytes than expected (or corrupt bytes); assert the verification step rejects the flip to `UPLOADED`, row remains `PENDING`/reconciles to `FAILED`.

## Clean-room Docker verification (required before v0.5.0 tag)
`docker compose down -v && docker compose up --build` with MinIO and the Reconciler added as new services. Demonstrate: register a dataset version (real upload to real MinIO), create a training run referencing it, let it flow through the existing V0.2/V0.3/V0.4 pipeline to `SUCCEEDED`, produce and upload an artifact, explicitly register it as a model version, query full lineage end-to-end via `GET /v1/models/{id}/versions/{n}/lineage` and confirm it correctly shows the dataset version, config, code commit, job/attempt, and artifact -- via real infrastructure, not mocked.
