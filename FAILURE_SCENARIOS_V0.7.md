# FAILURE SCENARIOS — V0.7

| # | Failure | Expected behavior |
|---|---|---|
| 1 | API dies after evaluation creation | Durable Job/outbox state survives; relay eventually dispatches |
| 2 | Kafka unavailable | Outbox remains durable; evaluation is not lost |
| 3 | Worker dies after claim | Lease expires; Recovery marks attempt LOST and retries |
| 4 | Evaluator subprocess crashes | Worker observes non-zero exit; retry policy classifies failure |
| 5 | Evaluator hangs | Worker supervision/lease machinery prevents indefinite ownership; Recovery handles stale job |
| 6 | Evaluator writes after fencing | Conditional ownership update affects zero rows |
| 7 | Duplicate evaluation delivery | Existing claim/idempotency prevents duplicate logical execution |
| 8 | DB unavailable during metric write | Write fails; no false success; retry/recovery handles execution |
| 9 | Model artifact unavailable | Evaluation fails closed; no metrics/gate PASS is emitted |
| 10 | Dataset artifact unavailable | Evaluation fails closed |
| 11 | Corrupt model artifact | Hash/consistency validation rejects model input |
| 12 | Corrupt dataset artifact | Hash/consistency validation rejects dataset input |
| 13 | Partial per-example writes before crash | Retry cannot create duplicate logical result rows; uniqueness protects identity |
| 14 | Partial metric aggregation before crash | Retry recomputes/repairs aggregate metrics from authoritative results according to implementation contract |
| 15 | Cancellation races completion | Existing fenced terminal write determines the winner; stale completion is rejected |
| 16 | Quality gate references missing metric | Gate becomes ERROR, never PASS |
| 17 | Candidate/baseline dataset mismatch | Gate comparison rejected as incompatible |
| 18 | Candidate/baseline config mismatch | Baseline comparison rejected as incompatible |
| 19 | Quality-gate worker crashes | Gate remains retryable/non-terminal until a valid evaluation completes |
| 20 | Duplicate gate evaluation | Unique identity makes the logical gate decision idempotent |
| 21 | Stale gate evaluator writes after reclamation | Fenced write rejected |
| 22 | Evaluation succeeds but gate evaluation fails | Evaluation remains SUCCEEDED; gate has explicit ERROR state |
| 23 | Gate FAIL | Evaluation remains valid; no automatic model promotion |
| 24 | Floating-point variation | Determinism tolerance is explicit; release evidence reports actual values and tolerance |
| 25 | Evaluator code/config changes | New immutable EvaluationConfig/code identity; old runs remain reproducible |

## Release-blocking scenarios
At minimum, scenarios 3, 4, 6, 7, 13, 15, 17, 18, and 21 require dedicated tests. Scenario 6 and 21 are stale-write/fencing tests and should be treated at the same release-blocking tier as V0.3/V0.6 split-brain tests.
