# Checkpoint / Resume Model — V0.6

Operational specification of ADR 015. See ADR 015 for full rationale; this document is the concrete schema/query shape.

## Discovery operates on the trusted `checkpoints` relationship, never on raw object storage
Discovery's query (below) joins `checkpoints` to `artifacts` -- it never scans object storage or the `artifacts` table alone. An `UPLOADED` artifact with no corresponding `checkpoints` row (e.g. because its registration was rejected by fencing, ADR 016, after a worker was reclaimed mid-upload) is invisible to discovery entirely, by construction -- not because a rule filters it out, but because the query has no path to it at all without a `checkpoints` row. This is the precise answer to "physical artifact existence is not the same as authoritative training state": the only way an artifact becomes a resume candidate is through the fencing-conditioned `checkpoints` INSERT succeeding.

## Checkpoint identity
A checkpoint is: one row in `checkpoints` (DB_SCHEMA_CHANGES_V0.6.md), referencing one `artifacts` row (`artifact_type='CHECKPOINT'`, V0.5 unmodified), scoped to `(training_run_id, attempt_number, step)`.

## The six compatibility rules, as a single query
```sql
SELECT c.* FROM checkpoints c
JOIN artifacts a ON a.id = c.artifact_id
WHERE c.training_run_id = :training_run_id
  AND a.status = 'UPLOADED'                                    -- rule 1
  AND c.base_model_id IS NOT DISTINCT FROM :run_base_model_id   -- rule 4
  AND c.base_model_version_number IS NOT DISTINCT FROM :run_base_model_version_number
  AND c.checkpoint_format_version = :supported_format_version   -- rule 6
ORDER BY c.step DESC, c.attempt_number DESC, c.created_at DESC
```
**Required clarification: deterministic ordering, precisely.** `step` is the primary, semantically-meaningful ordering key -- a higher step is strictly more trained-on-data than a lower one, which is what actually matters for resume, not wall-clock recency. `attempt_number DESC` is the tie-breaker for the (expected to be rare, but must still be deterministic) case where two checkpoints could carry the identical `step` value -- the more recent attempt's checkpoint is preferred, since it reflects the training run's latest known-good lineage. `created_at DESC` is a final backstop for the practically-impossible case both `step` and `attempt_number` tie (should not happen given the composite PK's uniqueness per `(training_run_id, attempt_number, step)`, but an explicit total order is cheap and removes any theoretical ambiguity). `created_at` alone, without `step` first, is explicitly rejected -- wall-clock time is not the authoritative signal for "which checkpoint represents more training progress," `step` is.
Rule 2 (hash re-verification) and rule 5 (config match, trivially true per ADR 015) are checked in application code after this query returns candidates -- rule 2 specifically requires downloading and re-hashing the artifact bytes, which is only worth doing for the top candidate the query already narrowed down to, not every row.

## Discovery algorithm (precise)
```
candidates = the query above, ordered step DESC
for candidate in candidates:
    bytes = download(candidate.artifact.storage_key)
    if hash(bytes) != candidate.artifact.content_hash:
        log "checkpoint hash mismatch, skipping" ; continue
    # rule 5, structural safety net (ADR 015) -- always true today, checked anyway:
    if candidate's implied config snapshot != current TrainingRun.training_config:
        log "checkpoint config mismatch, skipping" ; continue
    return candidate  # this is the resume target
return None  # no compatible checkpoint -- train from scratch
```
This is a linear scan in step-descending order, stopping at the first fully-valid candidate -- not an attempt to find "the best" checkpoint by any metric other than recency, which is the only ordering that makes sense for resuming a single, linear training run.

## What "training from scratch" means when a base_model_id is set
If `TrainingRun.base_model_id`/`base_model_version_number` is set (fine-tuning an existing registered model) and discovery finds no compatible checkpoint, "from scratch" means loading that base model's artifact -- not literally an untrained model. "Scratch" is relative to *this training run's own checkpoint history*, not relative to the base model's own weights.

## Distinguishing the final artifact from checkpoints
- `artifacts.artifact_type` (`CHECKPOINT` vs `MODEL`) is the coarse distinction (V0.5, unchanged).
- `training_run_outputs` (new, ADR 016) is the precise one: exactly one row per `TrainingRun`, `(training_run_id, final_artifact_id)`, inserted fencing-conditionally exactly once on successful completion. A `TrainingRun` with no `training_run_outputs` row has not (yet, or ever) successfully completed -- regardless of how many `CHECKPOINT` rows exist for it.
- Registering a final artifact as a `ModelVersion` (V0.5's existing, unmodified `POST /v1/models/{id}/versions`) is a *separate*, later, human/pipeline-driven decision -- `training_run_outputs` existing does not imply a `ModelVersion` exists (STATE_TRANSITIONS_V0.6.md restates V0.5's "training completion != model registration" for the new final-artifact concept specifically).

## Checkpoint format version
A small integer (`CHECKPOINT_FORMAT_VERSION` config, ARCHITECTURE_V0.6.md) this codebase controls -- not tied to any ML framework's own checkpoint format versioning (PyTorch, PEFT/LoRA library versions, etc.). It exists so that if V0.7+ ever changes *how* this platform structures a checkpoint (e.g., what metadata accompanies the weight file), old checkpoints aren't silently mis-loaded by newer code -- rule 6 rejects a mismatch outright rather than attempting a risky best-effort load.
