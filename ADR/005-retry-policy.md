# ADR 005: Retry Policy and Failure Classification

## Status
Proposed -- pending user review before implementation.

## Context
V0.2's simulated executor either succeeds or (for `job_type=simulate_failure`) fails, with no distinction between failure kinds and no retry at all -- a `FAILED` job just stays `FAILED`. V0.3 needs a retry engine, but blind retry-everything is dangerous: a permanently broken job (bad config, invalid input) retried repeatedly wastes worker capacity and can retry-storm the system. Not retrying anything is equally wrong: transient failures (worker lost, timeout, temporary dependency failure) should recover automatically.

## Decision: three-way classification, decided at the point of failure
When an attempt ends in `FAILED`, classify the cause into exactly one of:
- **transient** -- retry, subject to `MAX_ATTEMPTS` and exponential backoff. Examples: worker lost (lease expired, reclaimed -- this is always classified transient, since a lost worker tells us nothing about the job's own validity), network timeout, temporary dependency failure.
- **permanent** -- fail immediately, no retry, straight to DLQ. Examples: invalid configuration, invalid input, a bug that will reproduce identically on every attempt.
- **unknown** -- default when the executor doesn't explicitly classify. Treated as transient but *only* for retry purposes -- it still burns down `MAX_ATTEMPTS` like any transient failure, so an unknown-but-actually-permanent failure doesn't loop forever, just retries a bounded number of times before landing in the DLQ same as an exhausted transient failure would. This is the safe default: never worse than bounded retry, never infinite.

V0.3's simulated executor exposes classification via job_type, same pattern as V0.2's `simulate_failure`: `simulate_transient_failure` and `simulate_permanent_failure` job types, so the classification path can be tested deterministically without real executors existing yet (those come in V0.6+, and will need to actually classify their own exceptions -- documented as a constraint on future executor code, not solved now).

## Decision: exponential backoff with jitter (exact policy)
This is the one part of V0.3 that must be a precise, testable schedule, not just "a number recorded somewhere" -- a `next_retry_at` timestamp that nothing actually enforces is not backoff, it's a decoration. The enforcement point is the claim UPDATE (ADR 004): it must gain `AND next_retry_at <= now()` as an additional condition, alongside `status='QUEUED' AND cancel_requested=false`. A job whose retry isn't due yet simply cannot be claimed -- not by a worker's normal `job.queued` consumption, not by anything else. This is the same "the conditional UPDATE's WHERE clause is the whole enforcement mechanism" pattern used everywhere else in V0.2/V0.3, applied to time instead of state or ownership.

**Formula:**
```
uncapped   = BASE_DELAY_SECONDS * 2^(attempt_number - 1)
capped     = min(uncapped, MAX_DELAY_SECONDS)
jitter     = random_uniform(0, capped * JITTER_RATIO)
next_retry_at = now() + capped + jitter
```
Parameters, all configurable: `BASE_DELAY_SECONDS` (default 2), `MAX_DELAY_SECONDS` (default 60), `JITTER_RATIO` (default 0.2, i.e. up to +20% randomized on top of the capped delay). Jitter is full-additive-random on top of the capped exponential value (not "equal jitter" or "decorrelated jitter" schemes) -- simplest option that already solves the thundering-herd problem the requirements called out (1000 jobs failing simultaneously must not all retry at exactly the same instant); more sophisticated jitter strategies are unneeded complexity without load-test evidence they matter (V1.0's job to prove otherwise).

**Why jitter is not optional here (reversing the earlier draft's "deferred" call):** the review that approved this design pointed out that *un*-jittered backoff isn't really backoff at scale -- a synchronized retry-storm is a realistic V0.3-scale failure mode (e.g. a transient dependency blip failing many concurrently-running jobs at once), not a hypothetical needing a future load test to justify. Jitter is cheap (one random draw) and directly prevents a concretely-named failure mode, unlike more speculative additions -- so it's in scope now.

**Where the attempt becomes `LOST` vs `FAILED` (timing, restated precisely):** `LOST` is written by the Recovery process at the moment it successfully reclaims a stale lease (ADR 006) -- it is never written by the worker itself (a worker that's actually still running has no way to know its lease lapsed until its next fencing-conditioned write fails). `FAILED` is written by the worker itself, only when its own execution body raised/returned an error. These are structurally distinct code paths writing to the same `attempts.status` enum -- there's no ambiguity about "which one applies" because only one of the two processes can ever observe each situation.

## Decision: max attempts and DLQ
`MAX_ATTEMPTS` (default 3). On a transient failure, if `attempt_number >= MAX_ATTEMPTS`, the job is *not* retried again -- it transitions to `FAILED` (terminal) and gets a `dlq` row. Permanent classification always skips straight to `FAILED` + DLQ regardless of `attempt_number`.

## Where classification and retry decisions happen
The **worker** decides, at the moment its attempt fails -- not the recovery process. The recovery process only handles the "worker disappeared without reporting anything" case (ADR 004); it never classifies a failure reason it didn't observe (a lease timeout tells you the worker vanished, not why the job itself failed, so the recovery process always classifies its own reclamation-triggered failures as transient, per the "worker lost" example above -- it has no other information to work with).

The worker's finalize-attempt write (ADR 004's fencing-conditioned terminal UPDATE) is extended: instead of always writing a terminal job status, on a transient-and-retryable failure it writes `status='QUEUED'`, `next_retry_at=<computed>`, still fencing-conditioned on `(lease_owner, attempt_number)` exactly like any other terminal write -- "go back to queued for retry" is itself a state this fencing mechanism must protect, otherwise a fenced-out worker could wrongly re-queue a job that a different attempt already completed.

## Alternatives considered
- **Retry classification via HTTP-style status codes:** rejected as unnecessary indirection -- job execution isn't a network call, and inventing a status-code taxonomy for internal failures adds a translation layer with no benefit over a direct enum.
- **Infinite retry with no cap:** rejected outright -- this is the retry-storm failure mode the requirements explicitly warn against.
- **No jitter:** rejected -- see above; a synchronized retry-storm across concurrently-failing jobs is a concrete, nameable failure mode at V0.3's scale, not a hypothetical requiring load-test evidence first.
- **Equal/decorrelated jitter (AWS-style advanced jitter algorithms):** rejected as unneeded complexity -- simple additive random jitter already breaks lockstep retries; more sophisticated schemes solve problems (e.g. avoiding jitter-induced clustering under adversarial conditions) V0.3 has no evidence of.

## Consequences
- Every future real executor (V0.6+) must explicitly classify its own failures (raise a typed exception distinguishing transient/permanent) or accept the "unknown" default's bounded-retry behavior -- this is a contract documented here for future implementers, not enforced by any type system in V0.3 (V0.3's simulated executor is the only caller).
- The DLQ is a natural place to route "should a human look at this" jobs; V0.3 does not build any DLQ *processing* (redrive, manual retry-from-DLQ) -- it only records enough information to inspect (see DB_SCHEMA_CHANGES_V0.3.md). DLQ tooling is future scope.
