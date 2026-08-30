# ADR 015: Checkpoint/Resume — Retry Is Not Resume

## Status
Proposed -- pending user review before implementation. This is the central design problem of V0.6, alongside ADR 016.

## Context
V0.3 already defines "retry": a job's attempt fails transiently, `attempt_number` advances, a new attempt runs (ADR 004/005). That mechanism is unmodified in V0.6. What V0.6 adds is a *choice* the new attempt's training script makes: should it start training from the base model (as if this were the first attempt), or should it load weights from a checkpoint a *previous* attempt produced? These are different concepts and must not be conflated.

## Decision: retry and resume are independent
> **A retry (new attempt) never automatically implies resume. A resume is an explicit decision, made by the training subprocess at startup, to load a specific, immutable checkpoint artifact -- gated by compatibility rules checked at that moment, not assumed from context.**

Concretely: when a new attempt's training subprocess starts, it runs a **checkpoint discovery** step (below) *before* deciding whether to load base-model weights or checkpoint weights. If discovery finds no compatible checkpoint, the attempt trains from scratch (base model) -- this is a completely valid, non-error outcome, not a fallback-from-failure. If discovery finds one, the attempt loads it and continues from its recorded step. Either way, this is one `TrainingRun`, one lineage chain -- resuming does not create a new `TrainingRun` (that would break the "training config is immutable per run" model V0.5 already established); it only affects which weights the training loop starts from and which step number training metrics continue at.

## Checkpoint compatibility (exact rules, required clarification)
A checkpoint is eligible for resume if and only if **all** of:
1. Its artifact `status = 'UPLOADED'` (ADR 013's hard invariant -- never resume from a `PENDING`/`FAILED` artifact).
2. Its artifact's hash is re-verified at resume time (re-hash the downloaded bytes, compare to `artifacts.content_hash`) -- not merely trusted from the `UPLOADED` flag (ADR 013's "a consumer needing a stronger guarantee can re-verify at read time," applied here because loading corrupted weights into a real training loop is a much worse failure than a stale metadata read).
3. It belongs to the **same `training_run_id`** (checkpoints from a different training run, even for the same model, are never candidates -- a training run's checkpoints are its own history, not a shared pool).
4. The checkpoint's recorded base-model identity (`model_id`/`model_version` it started from, stored in the `checkpoints` table -- DB_SCHEMA_CHANGES_V0.6.md) matches the `TrainingRun`'s own `base_model_id`/`base_model_version_number` (or both are absent, for a from-scratch/no-base-model run).
5. The checkpoint's recorded training configuration snapshot matches the `TrainingRun.training_config` currently in effect -- trivially true in V0.6 since `TrainingRun` is immutable (ADR 010/V0.5) and every attempt of the same run shares the identical config; this check exists as a structural safety net, not because configs are expected to differ within one run.
6. The checkpoint's recorded format/version tag (`checkpoint_format_version`, a small integer this project controls, not tied to any specific ML library's own versioning) is one this version of the training code knows how to load.

If any check fails, that checkpoint is not a candidate -- discovery moves to the next-most-recent one, or concludes none exist and trains from scratch. **A discovery process must never partially trust a checkpoint** (e.g., load weights but ignore a stale config) -- it's all-or-nothing per checkpoint.

## Checkpoint discovery (selecting "the latest valid checkpoint")
```
SELECT checkpoints for this training_run_id
  ORDER BY step DESC
for each candidate, in that order:
  check all 6 compatibility rules above
  if all pass: this is the resume target, stop
  if any fails: skip, try the next-most-recent candidate
if none pass: train from scratch
```
This is a deterministic, read-only query over immutable data (checkpoints, once `UPLOADED`, are never modified) -- **no race is possible between two attempts both running discovery**, because there is nothing to race over: both would independently compute the identical answer from the identical immutable data. The only thing that ever races is *which attempt gets to be the one running discovery at all*, and that's already solved by V0.3's claim fencing (exactly one attempt holds the job at a time).

## Why retry must never silently change training configuration
`TrainingRun` is immutable (V0.5, unchanged) -- `training_config`, `code_commit`, `container_image`, `dataset_version_id`, `base_model_id`, `random_seed` are all fixed at creation. Since a retry reuses the *same* `TrainingRun` row (V0.3: retry is a new attempt of the same job, not a new job), it is **structurally impossible** for a retry to change any of these fields -- there is no code path that would let it, because there is no update path on `TrainingRun` at all. This is not new logic V0.6 must build; it's the direct, already-existing payoff of a decision V0.5 made for exactly this reason.

**If someone genuinely wants different config (a different learning rate, a different dataset version, etc.), the correct action is creating a new `TrainingRun`** (and thus a new job), not retrying the old one -- consistent with "training completion != model registration," this ADR adds: "different config != the same training run." A new `TrainingRun` naturally starts its own checkpoint lineage (different `training_run_id`), so its discovery step will never find the old run's checkpoints eligible (compatibility rule 3).

## Alternatives considered
- **Automatic resume on every retry (no explicit discovery/compatibility check):** rejected -- this is exactly the "retry implies resume" conflation the requirements explicitly reject. It would also silently break the moment any checkpoint were ever corrupted or incompatible, with no fallback.
- **A "resume_from_checkpoint_id" field the caller specifies at retry time:** rejected as the *primary* mechanism -- it reintroduces a manual step for something that should be automatic and deterministic (discovery), and it invites a caller to specify an incompatible checkpoint by mistake, which the automatic compatibility check exists precisely to prevent. (A future version could expose this as an *override* for operators, but that's not V0.6's default path.)
- **Storing full training state (optimizer state, RNG state) as part of "checkpoint compatibility," requiring exact byte-level environment match:** rejected as excessive for V0.6's scope -- LoRA/QLoRA checkpoints are small adapter weights, not full optimizer state; V0.6 checks the compatibility dimensions listed above, which are what actually matter for correctness (loading the right weights onto the right base model with the right config), not bit-for-bit environment reproduction (which this project has never claimed anywhere, including V0.5's own "reproducibility metadata, not bit-for-bit reproducibility" stance).

## Recording the discovery outcome (lineage completeness, required by review)
Discovery's result -- which checkpoint (if any) an attempt resumed from -- is itself recorded, once per attempt, in `attempt_resume_decisions` (DB_SCHEMA_CHANGES_V0.6.md), written fencing-conditionally by the Worker at attempt startup (same mechanism as checkpoint/output registration, ADR 016). Without this, a lineage query would have to *re-run* discovery after the fact to guess what an old attempt did -- fragile if discovery's logic ever changes, and not a faithful historical record (an attempt's actual resume decision is a fact about what happened, not a value that should be recomputed from current state). This makes the full per-attempt lineage queryable directly:
```
TrainingRun
  |-- Attempt 1 -> checkpoint at step 200 (registered)
  |-- Attempt 1 -> LOST (worker died)
  |-- Attempt 2 -> attempt_resume_decisions: resumed_from_step=200
  |-- Attempt 2 -> checkpoint at step 300 (registered)
  |-- Attempt 2 -> SUCCEEDED, training_run_outputs -> final artifact
```

## Consequences
- The `checkpoints` table (DB_SCHEMA_CHANGES_V0.6.md) must record enough to check all 6 rules: `training_run_id`, `step`, `artifact_id`, `base_model_id`/`base_model_version_number` snapshot, `checkpoint_format_version`. It does not need to store the full training config redundantly (rule 5 is checked against the live, immutable `TrainingRun` row, not a stored copy).
- `attempt_resume_decisions` is an append-only historical record, same posture as `attempts`/`checkpoints` themselves -- never updated after being written once per attempt.
- Discovery is O(number of checkpoints for this run) in the worst case (skipping incompatible ones) -- acceptable at V0.6's scale (a single small training run has, at most, a handful of checkpoints); revisit only if a real scenario produces so many checkpoints this matters.
