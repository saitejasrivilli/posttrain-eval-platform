# SCHEDULING POLICY — V0.7

Evaluation jobs use V0.4's resource-aware scheduler. V0.7 does not introduce a second scheduler or a special evaluation queue.

## Resource request
Default single-GPU evaluation request should be explicit in EvaluationConfig or job metadata. CPU and memory requirements are also recorded so the scheduler reserves what the evaluator actually needs.

## Priority
Evaluation inherits the same priority and bounded-aging policy as training jobs. A production implementation may assign a lower default priority to evaluations than interactive/training recovery work, but this is policy configuration, not a new scheduling primitive.

## Isolation
An evaluation job receives its own reservation keyed by `(job_id, attempt_number)`. Retries receive fresh reservations exactly as training retries do.

## No preemption
V0.7 does not preempt a running training job to make room for evaluation.
