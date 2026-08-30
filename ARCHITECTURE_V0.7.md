# ARCHITECTURE — V0.7 Evaluation Control Plane

## Architectural principle
V0.7 adds evaluation as another durable workload. It does not create a second execution system. Evaluation uses the existing Job -> Scheduler -> Worker -> supervised subprocess path from V0.2-V0.6, with new evaluation-specific persistence and fencing-conditioned result writes.

## Component diagram
```text
                         API
                          |
                          v
                    PostgreSQL
              /          |          \
     EvaluationConfig  EvaluationRun  QualityGate
                          |
                          v
                         Job
                          |
                     Scheduler
                          |
                       Worker
                          |
                evaluator subprocess
                    /           \
                   v             v
              ModelVersion   DatasetVersion
                   |             |
                   +------+- ----+
                          |
                          v
                per-example results
                          |
                          v
                 aggregate metrics
                          |
                          v
                    Quality Gate
                    /          \
                  PASS         FAIL
```

## Reused mechanisms
- Job state machine: V0.2/V0.3.
- Transactional outbox and at-least-once delivery: V0.2.
- Claim/lease/heartbeat/fencing/recovery/retry/DLQ: V0.3.
- Resource reservation and priority scheduling: V0.4.
- Immutable dataset/model/artifact identities: V0.5.
- Supervised subprocess and second fencing layer: V0.6.

## Evaluation flow
1. API validates references to an immutable ModelVersion and DatasetVersion.
2. API creates immutable EvaluationConfig and EvaluationRun plus a Job/outbox event atomically where applicable.
3. Scheduler reserves required resources.
4. Worker claims the job and starts the heartbeat loop.
5. Worker launches evaluator subprocess with immutable IDs, worker identity, attempt number, and evaluation config.
6. Subprocess loads the exact model artifact referenced by ModelVersion and exact dataset artifact referenced by DatasetVersion.
7. Subprocess evaluates examples and emits structured reports.
8. Worker accepts reports only while its lease/fencing identity is valid.
9. Per-example results and aggregate metrics are persisted using conditional ownership predicates.
10. On successful completion, the Worker finalizes the attempt and the evaluation run becomes terminal.
11. Quality-gate evaluation consumes the stored immutable metrics and writes a durable decision.
12. ModelVersion is not mutated and no automatic production promotion occurs.

## Stale-worker rule
Every evaluator result write must require:
```text
status = RUNNING
AND lease_owner = :worker_id
AND attempt_number = :attempt_number
```
inside the same write operation. There is no check-then-write sequence.

A stale subprocess may finish computation and even hold valid bytes/results locally, but those results cannot become trusted platform state after its fencing identity is lost.

## Reproducibility identity
An EvaluationRun is reproducible from:
```text
model_id + model_version
+ dataset_id + dataset_version
+ evaluation_config_id/version
+ evaluator_code_commit
+ evaluator/container image
```
The run must also record the actual attempt that produced the result.

## Quality-gate boundary
Quality gates are downstream of evaluation persistence. The evaluator does not decide whether a model should be promoted while it is producing metrics. The gate reads immutable stored metrics and evaluates explicit rules. This separates measurement from policy.

## Failure boundaries
```text
API crash after durable creation      -> existing outbox durability
Kafka/relay failure                   -> existing outbox retry
Worker crash                          -> V0.3 lease recovery
Evaluator subprocess crash            -> Worker detects exit; existing retry/recovery
Stale evaluator                       -> fencing rejects writes
Duplicate delivery                    -> existing claim/idempotency behavior
Object-store failure                  -> existing artifact semantics
DB failure                            -> existing fail-closed behavior
Quality-gate calculation failure      -> evaluation remains recorded; gate remains non-terminal/failed explicitly
```

## Deliberate simplifications
- No separate evaluation microservice.
- No workflow/DAG engine.
- No generic graph lineage.
- No LLM-as-a-judge.
- No distributed evaluation.
- No automatic promotion.
- No streaming metrics service.
- No new message broker.
