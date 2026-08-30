# Training Configuration — V0.6

## Fields captured in `TrainingRun.training_config` (JSONB, V0.5 schema, unmodified column)
```json
{
  "model": "base model identifier (HF hub id, or implied by base_model_id/version)",
  "lora": {
    "r": 8,
    "alpha": 16,
    "dropout": 0.05,
    "target_modules": ["q_proj", "v_proj"]
  },
  "learning_rate": 0.0002,
  "batch_size": 4,
  "gradient_accumulation_steps": 4,
  "max_steps": 200,
  "checkpoint_every_n_steps": 50,
  "precision": "bf16",
  "optimizer": {"type": "adamw", "weight_decay": 0.01},
  "quantization": "4bit"  
}
```
This is a representative shape, not a rigid schema enforced by the database (still a JSONB blob, per V0.5's ADR -- "no separate `training_configs` table with its own versioning" -- unchanged). V0.6 does not add new columns to `training_runs` for these fields; they live inside the existing `training_config` JSON, consistent with V0.5's decision.

## Immutable fields (restated precisely, per the design review's explicit ask)
Every field above -- `model`, `lora.*`, `learning_rate`, `batch_size`, `gradient_accumulation_steps`, `max_steps`, `precision`, `optimizer.*`, `quantization` -- plus `dataset_version_id`, `base_model_id`/`base_model_version_number`, `code_commit`, `container_image`, `random_seed` (already-existing `TrainingRun` columns, V0.5) are **all** immutable once the `TrainingRun` is created. None of them can change across retries of the same run, because `TrainingRun` itself has no update path (ADR 010/STATE_TRANSITIONS_V0.5.md #3, unchanged) -- this is a single structural guarantee covering every field in this list at once, not twelve separate rules to enforce individually.

## "Training environment" (code version, container image)
`code_commit` and `container_image` (already `TrainingRun` columns, V0.5) are the project's answer to "training environment" -- the exact git commit and container image that produced a given run. V0.6 does not add finer-grained environment capture (e.g., pinned library versions beyond what the container image itself pins) -- the container image *is* the environment specification at this project's current scope; if per-dependency version drift becomes a real problem, that's a future version's concern with real evidence behind it (same discipline as every prior "don't build it until it's proven needed" decision in this project).

## If a user wants different configuration
Per ADR 015: create a new `TrainingRun` (and thus a new `Job`). It gets its own `training_run_id`, its own checkpoint lineage (empty at first), and cannot accidentally inherit or interfere with the old run's checkpoints (ADR 015's compatibility rule 3: same-`training_run_id` requirement). This is enforced structurally, not by a policy someone has to remember -- a new `TrainingRun` row simply has no checkpoints yet, and discovery against it will correctly find none.
