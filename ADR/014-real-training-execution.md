# ADR 014: Real Training Execution — Supervised Child Subprocess

## Status
Proposed -- pending user review before implementation.

## Context
V0.2/V0.3's Worker calls `_run_executor(job)` synchronously, in-process, and it's instant (a simulated no-op or short sleep). V0.6 replaces this with a real training loop that can run for minutes, allocates real GPU memory, and can genuinely hang, OOM, or crash the interpreter (a CUDA out-of-memory error, a corrupted checkpoint load, a bad dependency) in ways the simulated executor never could. Three execution models are possible: same process, same-process background thread, or a supervised child subprocess.

## Decision: supervised child subprocess
The Worker process (lease-holder, ADR 004's "interface separation" already anticipated this) spawns the actual training script as a child process (`subprocess.Popen`, not a thread) and supervises it: monitors its liveness, relays its reported progress (checkpoints, metrics) back into Postgres, and -- critically -- can send it a real OS signal (SIGTERM/SIGKILL) when the Worker's own lease is fenced out.

**Why not same-process (synchronous call, as V0.2-V0.5 do today):** a training crash (CUDA OOM, segfault in a native extension, an unhandled exception deep in a data-loading library) would take down the Worker process itself -- losing its ability to even report the failure cleanly, and more importantly, an in-process hang (a stuck DataLoader worker, a deadlocked CUDA context) cannot be forcibly terminated from within the same process. ADR 004's heartbeat-independence requirement becomes much harder to guarantee if the thing that might hang shares an address space and the GIL with the heartbeat loop.

**Why not same-process background thread (training on a thread, heartbeat on another):** Python cannot forcibly terminate another thread -- there is no safe `thread.kill()`. If a stuck training loop must be stopped after a lease is lost, a thread-based model has no real termination mechanism, only cooperative checking (which requires the training loop itself to periodically check a flag -- fragile, and easy to get wrong inside a third-party training library's internal loop where you don't control every iteration boundary).

**Why a subprocess works:** the OS gives a real, unconditional termination primitive (`SIGKILL`) that works regardless of what the child is doing internally -- stuck in a native CUDA call, deadlocked, anything. The Worker's heartbeat loop (ADR 004, unchanged) runs on its own thread as before, but now it's supervising a subprocess's liveness/lease instead of directly running training code -- so a training crash cannot corrupt the Worker's own heartbeat/fencing logic; they are different OS processes with independent failure domains.

## Structure
```
Worker process (unchanged role: claim, heartbeat, fencing, finalize -- ADR 004)
  |
  +-- heartbeat loop (own thread, unchanged from V0.3)
  |
  +-- training subprocess (NEW, V0.6)
        |
        +-- loads DatasetVersion artifact, base model (artifact or HF hub)
        +-- runs LoRA/QLoRA SFT loop
        +-- at each checkpoint interval: writes checkpoint locally, then
        |     calls back to the platform (via the existing V0.5 artifact
        |     upload path, ADR 013 -- same upload-lease/hash-verify
        |     mechanism, no new consistency logic) to upload it, THEN
        |     registers it in the new `checkpoints` table (fencing-
        |     conditioned on the Worker's current job attempt -- ADR 016)
        +-- reports metrics (step, loss, timestamp) back via a small,
              periodic callback -- stored in `training_metrics` (new table)
        +-- on completion: uploads final MODEL artifact via the same
              artifact-upload path, exits 0
```
The training subprocess is **not** a new kind of "attempt" or a new fencing entity -- it operates entirely within the Worker's single claimed attempt (`attempt_number`, `lease_owner`). Every write it triggers (checkpoint registration, metric recording, final artifact) must carry the Worker's current fencing credentials, re-verified at write time, exactly like every other V0.3/V0.4/V0.5 write (see ADR 016).

## Termination after lease loss (stated precisely -- required clarification)
When the Worker's heartbeat renewal fails (ADR 004's existing fencing check -- `repo.heartbeat()` returns `False`), the Worker:
1. Sends `SIGTERM` to the training subprocess, waits up to `TRAINING_TERMINATION_GRACE_SECONDS` (configurable) for it to exit.
2. If still alive, sends `SIGKILL`.
3. Does **not** wait for or trust any "graceful checkpoint save" from the subprocess during this window -- no code path assumes the subprocess successfully saves state on SIGTERM. If the subprocess happens to catch SIGTERM and save a checkpoint in time, that checkpoint still goes through the normal fencing-conditioned registration (step above) and will simply fail to register if the Worker has already been fenced out (same outcome as any other stale write, ADR 004/016) -- a "best-effort" save is harmless because it's still subject to the same fencing check, not because it's assumed reliable.
This is a hard requirement from the review: **do not claim graceful termination unless implemented and tested.** V0.6 implements forced termination (SIGTERM-then-SIGKILL) as the guaranteed mechanism; anything gentler is opportunistic, not relied upon.

## GPU verification (before training begins, not after)
Before the training subprocess does anything expensive, it verifies: a CUDA device is actually visible (`torch.cuda.is_available()`), device count and memory match or exceed what the Scheduler's reservation implied (V0.4's `reservations.gpu`/`memory_mb`). A mismatch (scenario: scheduler reserved a GPU but this worker's environment has none, or less memory than expected) is treated as an immediate transient failure of this attempt -- same retry/classification path as any other transient failure (ADR 005, unchanged), not a new failure category. This is intentionally simple: V0.6 does not attempt to reconcile *why* the environment didn't match (that's an operational/deployment concern), it only ensures the mismatch is detected and fails closed rather than silently training on the wrong hardware or OOMing uninformatively.

## Alternatives considered
- **Same-process, synchronous (status quo extended):** rejected, see above -- no real termination mechanism, shared failure domain with the Worker's own fencing logic.
- **Same-process thread:** rejected, see above -- no forcible termination.
- **A fully separate "Training Worker" process type, distinct from V0.3's Worker:** rejected -- would duplicate the entire claim/heartbeat/fencing apparatus for no benefit; the existing Worker already does exactly the right thing (claim an attempt, hold a lease, finalize), it just needs to delegate the actual execution to a subprocess instead of an in-process function call. Reusing the existing Worker role is simpler and keeps one fencing mechanism, not two.

## Consequences
- The Worker process now has a new responsibility (subprocess supervision: spawn, monitor, signal, relay) alongside its existing claim/heartbeat/finalize role -- this is an internal restructuring of `app/services/worker.py`, not a new external process/service (no change to `docker-compose.yml`'s process list beyond what the Worker container already needs, e.g. CUDA runtime availability).
- Every checkpoint/metric/final-artifact write originating from the subprocess must be re-verified against current fencing state at write time -- this is the single most important correctness rule this ADR introduces, detailed fully in ADR 016.
