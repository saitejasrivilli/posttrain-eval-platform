# REQUIREMENTS — V0.6 Real Post-Training Execution

## Objective
Execute a real, small-scale LLM post-training workload (LoRA/QLoRA SFT) on one GPU, using the existing job/attempt/lease (V0.3), resource scheduling (V0.4), and artifact/lineage (V0.5) infrastructure unmodified in its correctness mechanisms -- V0.6 replaces the *simulated* executor (`app/services/worker.py::_run_executor`) with a real one and adds the concepts real training needs (checkpoints, resume, GPU verification) on top of what already exists.

V0.1-V0.5 are released and stable. No prior invariant (fencing, atomic claim/reservation, artifact consistency, lineage immutability) is weakened.

## Scope discipline
- Single GPU only. No multi-GPU, DDP, NCCL, Kubernetes, Ray, Slurm, distributed training.
- No model serving, no inference autoscaling -- V0.6 ends at "a registered model version exists," not "it's servable."
- Workload deliberately small: a small open base model (e.g. a sub-1B or few-B parameter model) and a small dataset, chosen so a full run costs minutes of single-GPU time, not hours -- this is a correctness/infrastructure exercise, not a training-quality exercise.

## Required capabilities
1. Real GPU execution -- a real CUDA device, verified present and matching what the Scheduler (V0.4) reserved before training starts.
2. Real model loading (base model weights, from a location -- local cache or an existing V0.5 `ModelVersion`/artifact if fine-tuning a previously-registered model).
3. Real dataset loading -- from a V0.5 `DatasetVersion`'s artifact bytes.
4. Real LoRA/QLoRA (or comparably lightweight) SFT training loop.
5. Checkpoint creation at a configurable step interval.
6. Checkpoint artifact upload -- reuses V0.5's `artifacts` table (`artifact_type='CHECKPOINT'`) and consistency model (ADR 013) unmodified.
7. Training interruption (worker dies, lease lost) -- handled by the *same* V0.3 Recovery mechanism, unmodified.
8. Retry (a new attempt, per V0.3) -- does not automatically imply resume (see ADR 015).
9. Resume from a valid, explicitly-selected checkpoint -- a new attempt's training script may choose to load a prior checkpoint's weights, subject to compatibility rules (ADR 015).
10. Final model artifact creation (`artifact_type='MODEL'`) -- distinguished from intermediate checkpoints structurally, not just by convention (see TRAINING_EXECUTION_MODEL_V0.6.md).
11. Training metrics captured (loss, step, timestamps at minimum) -- stored as structured data attached to the attempt, queryable, not just printed to logs.
12. GPU/resource verification -- the training subprocess verifies the environment actually has what the Scheduler believed it reserved, before consuming any training time.
13. Complete V0.5 lineage preservation -- a real training run produces the exact same lineage chain (`DatasetVersion -> TrainingRun -> Job/Attempt -> Artifact -> ModelVersion`) V0.5 already established, just with real artifacts instead of a `b"model weights"` test fixture.

## What must NOT change
- V0.3's fencing mechanism (`attempt_number`, `lease_owner`, conditional UPDATEs).
- V0.4's reservation/scheduling mechanism.
- V0.5's artifact consistency model (metadata-first, content-addressed, upload lease, Reconciler) and lineage model (fixed FK chain).
- `TrainingRun` immutability (V0.5 ADR, STATE_TRANSITIONS_V0.5.md #3).

## Explicit non-goals for V0.6
- Multi-GPU, DDP, NCCL, any distributed training strategy.
- Kubernetes, Ray, Slurm as an execution backend.
- Model serving / inference endpoints / autoscaling.
- Hyperparameter search, distributed data loading, dataset streaming from remote sources beyond what a single-GPU small workload needs.
- Automatic quality gating on training metrics (that's V0.7's evaluation/quality-gate scope).
- Checkpoint pruning/retention policy (old checkpoints accumulate; cleanup is future scope, consistent with V0.5's "no auto-deletion" stance on artifacts generally).

## Acceptance criteria (must all pass before v0.6.0 tag)
- [ ] A real LoRA/QLoRA SFT training run executes end-to-end on one real GPU, producing a final model artifact registered as a `ModelVersion` with complete lineage
- [ ] Checkpoints are created during training, uploaded as `CHECKPOINT` artifacts, and are distinguishable from the final `MODEL` artifact
- [ ] Worker killed mid-training -> V0.3 Recovery reclaims (unmodified mechanism) -> retry (new attempt) -> either resumes from a compatible checkpoint or restarts from scratch, per explicit, tested compatibility rules
- [ ] A retry never silently changes any immutable training config field (model, dataset, learning rate, batch size, LoRA config, precision, optimizer config, max steps, code version, environment) -- proven by `TrainingRun` immutability, unmodified from V0.5
- [ ] GPU verification: a worker whose actual environment doesn't match what was reserved fails closed, does not silently proceed
- [ ] Checkpoint corruption / hash mismatch is detected and such a checkpoint is never selected for resume
- [ ] Two retries racing to consider resuming the same checkpoint -- deterministic, safe (checkpoints are immutable, read-only once `UPLOADED`, no race possible on the read side; the only race is which attempt claims the job, already solved by V0.3)
- [ ] Stale worker (fenced out) cannot register a checkpoint or finalize a model artifact against a job it no longer owns
- [ ] Cancellation during checkpoint/final-artifact creation behaves per the same cooperative, last-checkpoint-wins model V0.2/V0.3 already established -- no new cancellation mechanism invented
- [ ] Live clean-room demonstration on real hardware: full run, an induced worker kill mid-training, recovery, retry/resume, completion, lineage query
- [ ] All numbers/claims (training time, GPU memory used, actual step counts) trace to actual run output -- never estimated
