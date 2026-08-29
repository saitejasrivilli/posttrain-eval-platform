# ADR 004: Worker Leases and the Fencing-Token Ownership Mechanism

## Status
Proposed -- pending user review before implementation.

## Context
V0.2 left an explicit, documented gap: if a worker dies after claiming a job (`QUEUED -> RUNNING`), the job stays `RUNNING` forever -- no automatic recovery. V0.3 must fix this, but the naive fix is dangerous:

```
UPDATE jobs SET status = 'QUEUED' WHERE lease_expires_at < now();
```

This alone does not solve the problem. It only tells you the *lease* expired -- it tells you nothing about whether the worker actually stopped. A worker can be alive but partitioned from Postgres (network split), or paused (GC, scheduler preemption) long enough for its lease to lapse, then resume and try to write its result as if nothing happened. If that write succeeds, you get **split-brain**: two workers both believe they own (or owned) the job, and the reclaiming worker's already-finalized result can be silently overwritten or duplicated by the original worker's late write.

## Terminology
`attempt_number` is called a **fencing token**, not merely an attempt counter. Its defining property: every ownership acquisition (claim or reclaim) issues a monotonically newer token, which makes every write made under a previous token stale and rejectable by the database itself -- no coordination with the previous owner required, no need for it to even know it's been superseded.

## Decision: attempt_number as both retry-tracking counter and fencing token
`jobs.attempt_number` serves two purposes at once, deliberately, not as two separate mechanisms:
1. It counts which attempt is current (retry/attempt-tracking requirement).
2. It is the **fencing token**: every write a worker makes for a job must be conditioned on the attempt_number it believes it owns still being the current one in the database.

**Ownership is defined precisely as:** a worker owns a job if and only if, at the moment of any write, `jobs.lease_owner = <this worker's id>` AND `jobs.attempt_number = <the attempt_number this worker received when it claimed>` AND `jobs.status = 'RUNNING'`. All three conditions are checked atomically in the same conditional UPDATE that performs the write -- there is no separate "check ownership" step that could race with a "perform write" step.

### The three operations, all fencing-conditioned
```sql
-- Claim (first attempt from QUEUED, or a fresh retry attempt from QUEUED):
UPDATE jobs SET status='RUNNING', lease_owner=:worker_id,
       lease_expires_at=now()+:lease_duration, attempt_number=attempt_number+1
WHERE id=:job_id AND status='QUEUED' AND cancel_requested=false
RETURNING attempt_number;

-- Reclaim (stale-lease recovery, from RUNNING with an expired lease).
-- Branches on cancel_requested and MAX_ATTEMPTS in the SAME atomic statement,
-- so the ownership move and the retry/cancel/exhaust decision can never be
-- split across two writes that something could race between. Does NOT
-- increment attempt_number -- moving status away from 'RUNNING' is what
-- fences the old owner (see "why this solves split-brain" below); claim() is
-- the only place a NEW attempt actually starts, so it's the only place the
-- token advances. (An earlier draft of this ADR had reclaim also increment
-- attempt_number; that created a numbering gap -- an attempt_number that was
-- "spent" by reclaim but never had a real attempt run under it. Caught during
-- implementation, corrected here.)
UPDATE jobs SET
  lease_owner = NULL,
  lease_expires_at = NULL,
  status = CASE
             WHEN cancel_requested THEN 'CANCELLED'
             WHEN attempt_number >= :max_attempts THEN 'FAILED'
             ELSE 'QUEUED'
           END,
  next_retry_at = CASE
             WHEN cancel_requested OR attempt_number >= :max_attempts THEN NULL
             ELSE :computed_next_retry_at
           END
WHERE id=:job_id AND status='RUNNING' AND lease_expires_at < now()
RETURNING attempt_number, status;
-- (In the same transaction: insert an `attempts` row for this attempt_number
-- with status='LOST', error_classification='transient' -- this IS the attempt
-- that was running and got fenced out, not a "previous" one.)

-- Heartbeat (renew lease -- must prove continued ownership to succeed):
UPDATE jobs SET lease_expires_at=now()+:lease_duration
WHERE id=:job_id AND lease_owner=:worker_id AND attempt_number=:my_attempt_number
      AND status='RUNNING';
-- rowcount 0 => this worker has been fenced out. It must stop treating
-- itself as the owner and abandon any further writes for this attempt.

-- Terminal commit (SUCCEEDED/FAILED/CANCELLED):
UPDATE jobs SET status=:outcome
WHERE id=:job_id AND lease_owner=:worker_id AND attempt_number=:my_attempt_number
      AND status='RUNNING';
-- rowcount 0 => this worker's result is stale and MUST be discarded, not retried,
-- not logged as a conflict to resolve -- simply dropped. The job's current state
-- (whatever the reclaiming attempt produced) is authoritative.
```

**Why this solves split-brain:** a worker's heartbeat and terminal-commit statements require BOTH `status='RUNNING'` AND `attempt_number=<its own>`. Reclaim's job is to move `status` away from `'RUNNING'` -- the instant that commits, every subsequent write from the original worker fails to match (rowcount 0), regardless of whether `attempt_number` itself changed. A later claim() (by a genuinely new attempt) additionally advances `attempt_number`, which is what keeps the *next* real attempt's own writes scoped correctly and lets `attempts` stay a clean 1:1 log of real attempts -- but the fencing guarantee itself is enforced by the `status` condition, not the token bump. Postgres row-level locking on each single UPDATE statement is what makes this airtight: there is no window where two processes both read "status = RUNNING" and both proceed to write, because the write itself is the atomic check.

The original worker resuming after a network partition and trying to report "job succeeded!" is exactly the case ADR 004 exists to reject: its terminal-commit UPDATE carries the old `attempt_number`, which no longer matches, so it silently affects zero rows. The worker must be written to treat rowcount 0 as "I have been fenced, discard my result," never as "retry the write."

## Lease duration and heartbeat interval
Configurable via `LEASE_DURATION_SECONDS` (default 30) and `HEARTBEAT_INTERVAL_SECONDS` (default 5). Operational rule (documented, not code-enforced in V0.3): `LEASE_DURATION_SECONDS >= HEARTBEAT_INTERVAL_SECONDS * 3`, mirroring V0.1's own "3 missed heartbeats before marking dead" precedent from the original project roadmap.

**Tradeoff:** shorter lease -> faster recovery of genuinely dead workers, but higher risk of falsely reclaiming a slow-but-healthy worker (GC pause, scheduler preemption, network jitter delaying a heartbeat). Longer lease -> safer against false reclamation, slower recovery when a worker has actually died. There is no universally correct value; V0.3 picks conservative defaults and makes both configurable rather than guessing at a "correct" number -- any specific tuning claim would violate the project's no-invented-numbers rule without a load test to back it up (that's V1.0's job).

**Structural requirement this creates:** the worker's heartbeat must run on a schedule independent of the execution body's runtime -- it cannot be "send a heartbeat, then run the job synchronously, then send another." V0.2's simulated executor is instant, so this wasn't yet visible, but V0.3's design must state it as a hard constraint for whoever builds real (V0.6+) execution bodies: heartbeats run on their own timer/thread, execution runs on its own, and a slow-but-progressing execution must never starve the heartbeat.

**Interface separation (kept now, even though the implementation is simple):** V0.3's worker is structured as two independent concerns behind a small internal interface -- a lease-holder (owns the heartbeat loop and the fencing-conditioned writes) and an executor (runs the job body, reports success/failure back to the lease-holder). V0.3's executor is a simulated in-process function call. A future real executor (V0.6+) is expected to become a supervised child process (e.g. a training subprocess) that the lease-holder monitors rather than calls directly:
```
worker process (lease-holder)
  |-- heartbeat loop (own timer)
  |-- executor interface
        `-- V0.3: simulated in-process call
        `-- V0.6+: supervised child process (e.g. a PyTorch training subprocess)
```
The point of keeping this interface now is that the heartbeat loop's independence from the execution body is enforced by *structure* (they are different components communicating through an interface), not by convention inside one function -- so swapping the executor for a real subprocess-supervising one later does not require touching the lease/fencing code at all.

## Alternatives considered
- **Distributed lock service (etcd/Zookeeper/Redis-based lease):** rejected -- explicitly out of scope (no Redis in V0.3), and unnecessary: Postgres row-level locking already gives us the same atomicity guarantee we relied on for V0.2's claim race, at zero new infrastructure cost.
- **Trusting `lease_expires_at < now()` alone without a fencing token:** rejected -- this is exactly the naive approach that enables split-brain, described above.
- **Worker self-terminates on missed heartbeat ack (client-side enforcement only):** insufficient alone -- a partitioned worker can't learn it missed an ack (that's what partitioned means), so the guarantee must live in what the *database* accepts, not in what the worker promises to do. The fencing check on every write is the actual enforcement point; the worker's own heartbeat-failure self-abort is a nice-to-have optimization (stops wasted work sooner) but not the source of correctness.

## Consequences
- Every future write path a worker makes (heartbeat, terminal commit, and in later versions anything else) must carry `(lease_owner, attempt_number)` as a condition. This is a hard constraint on all future worker code, not just V0.3's.
- `attempt_number` becomes the single source of truth for "how many times has this job been attempted," used by both the retry-limit check (ADR 005) and the fencing mechanism -- one field, two jobs, deliberately (avoids maintaining two counters that could drift).
