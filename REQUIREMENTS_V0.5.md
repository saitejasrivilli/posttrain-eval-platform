# REQUIREMENTS — V0.5 ML Artifact & Lineage Platform

## Objective
Make lineage and reproducibility first-class: for any model, answer "exactly how was this produced" via a traceable chain to its dataset version, training config, code version, execution job/attempt, and evaluations -- not just CRUD registries bolted onto the existing job system.

V0.1-V0.4 are stable and unmodified except additively. The job/attempt/lease/scheduler machinery is the *execution engine* underneath training runs -- V0.5 does not replace it, it adds an ML-domain metadata layer that references it.

## Required concepts
- **Dataset** / **DatasetVersion** -- immutable once created, content-identified.
- **Model** / **ModelVersion** -- immutable once created, content-identified, optional base-model lineage.
- **Artifact** -- the actual bytes (dataset file, model weights, checkpoint), stored in object storage, referenced by content hash.
- **TrainingRun** -- ties a job/attempt to its inputs (dataset version, base model version, config, code commit) and its output (an artifact, once one exists).
- **Lineage** -- the traceable chain across all of the above, queryable per model version.

## Functional requirements
1. Dataset registration creates a `Dataset` (a name/description container) and its first `DatasetVersion` (immutable, content-hashed).
2. New dataset content creates a *new* `DatasetVersion`, never mutates an existing one.
3. Model registration is an **explicit, separate action** from training completion -- a successful training run produces an artifact; registering that artifact as a `ModelVersion` is a distinct step. See ADR 012/013 and STATE_TRANSITIONS_V0.5.md.
4. `TrainingRun` captures reproducibility metadata: `dataset_version_id`, `base_model_version_id` (nullable), `training_config`, `code_commit`, `container_image`, `random_seed`, `job_id` (the V0.3/V0.4 execution vehicle). This is metadata *about* reproducibility, not a claim of bit-for-bit reproducibility (that's never asserted -- see ARCHITECTURE_V0.5.md).
5. Artifacts are content-addressed (sha256 of bytes) -- identity is derived from content, not assigned arbitrarily. See ADR 011.
6. PostgreSQL stores all metadata (datasets, versions, models, training runs, artifact records). Object storage (MinIO, S3-compatible) stores all bytes. Model weights/checkpoints/dataset files are never written to PostgreSQL.
7. A `ModelVersion` may only reference an artifact whose upload has been confirmed complete (`status=UPLOADED`) -- never a pending or failed one. This is a hard invariant, same class as V0.4's "worker cannot claim without a valid reservation."
8. Every retried training attempt that produces a new artifact gets its own artifact record, linked to its specific attempt -- consistent with V0.3's `(job_id, attempt_number)` granularity.

## Non-functional requirements
- No Spark, Iceberg, Delta Lake, Databricks, Kubernetes, Ray, Slurm, multi-region storage.
- PostgreSQL and object storage are never claimed to be one atomic transaction. A precise, tested reconciliation model exists for every ordering of "one succeeded, the other didn't." See ADR 013.
- All V0.1-V0.4 invariants (fencing, lease, reservation, attempt-numbering) remain intact and untouched.

## Explicit non-goals for V0.5
- Real training execution (V0.6) -- V0.5's "training run" still executes via the existing simulated executor; V0.5 adds the metadata/lineage layer around it, not real SFT/DPO/GRPO.
- Evaluation and quality gates (V0.7) -- V0.5 defines where an evaluation *would* attach to a model version, but does not implement evaluation logic.
- Model promotion/release workflow (V0.8).
- A generic graph-based lineage engine -- the lineage chain has a fixed shape (dataset version -> training run -> artifact -> model version), represented by direct foreign keys, not a general-purpose graph model. See ADR 012 for why a generic graph is rejected as premature.
- Artifact deletion/garbage-collection tooling beyond what reconciliation needs for orphan detection.

## Acceptance criteria (must all pass before v0.5.0 tag)
- [ ] Dataset registration + versioning: two uploads of different content produce two immutable `DatasetVersion` rows, never a mutation of the first
- [ ] Artifact upload succeeds, metadata commit fails -> reconciliation detects and resolves (orphaned bytes, no dangling reference ever created)
- [ ] Metadata commits (PENDING), artifact upload fails or process dies -> reconciliation detects stuck `PENDING` artifact, resolves to `FAILED` or retries
- [ ] Duplicate artifact upload (same content) -> idempotent via content hash, no duplicate bytes stored, no duplicate metadata row
- [ ] Duplicate model/dataset version registration attempts -> idempotent or rejected deterministically, never silently creates two "versions" for identical content
- [ ] Two training runs referencing the same dataset version concurrently -> both succeed, no interference (dataset versions are immutable/read-only once `UPLOADED`)
- [ ] A `ModelVersion` can never reference an artifact that isn't `UPLOADED` -- enforced by a hard DB-level/query-level invariant, tested
- [ ] Training job cancelled mid-artifact-creation -> no `ModelVersion` ever created from a cancelled run's partial artifact
- [ ] Object storage unavailable -> artifact upload fails closed, metadata reflects `PENDING`/`FAILED` accurately, no silent data loss
- [ ] PostgreSQL unavailable -> same fail-closed posture as every prior version
- [ ] Orphaned artifacts (bytes with no live metadata reference) are detectable via reconciliation, not silently accumulating forever unaccounted-for
- [ ] Live clean-room demonstration: register a dataset version, run a (simulated) training job through the existing V0.3/V0.4 pipeline, produce an artifact, explicitly register it as a model version, query full lineage for that model version end-to-end
- [ ] All numbers/claims trace to actual test/run output (same rule as V0.1-V0.4)
