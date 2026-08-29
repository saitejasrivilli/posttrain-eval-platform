import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.job import JobStatus
from app.repository import attempts as attempts_repo
from app.repository import dlq as dlq_repo
from app.repository import jobs as repo
from app.retry_policy import PERMANENT, UNKNOWN, compute_next_retry_at, is_retryable

SIMULATED_FAILURE_JOB_TYPE = "simulate_failure"  # kept for V0.2 test compatibility -> transient
SIMULATE_TRANSIENT_FAILURE_JOB_TYPE = "simulate_transient_failure"
SIMULATE_PERMANENT_FAILURE_JOB_TYPE = "simulate_permanent_failure"


def _run_executor(job) -> tuple[str, str | None, str | None]:
    """V0.3's executor: still simulated (real bodies are V0.6+), but kept
    behind this function boundary deliberately -- see ADR 004's "interface
    separation." Returns (outcome, error_message, error_classification).
    outcome is one of SUCCEEDED/FAILED."""
    sleep_seconds = 0
    if job.config:
        sleep_seconds = job.config.get("sleep_seconds", 0)
    if sleep_seconds:
        time.sleep(sleep_seconds)

    if job.job_type in (SIMULATED_FAILURE_JOB_TYPE, SIMULATE_TRANSIENT_FAILURE_JOB_TYPE):
        return JobStatus.FAILED.value, "simulated transient failure", "transient"
    if job.job_type == SIMULATE_PERMANENT_FAILURE_JOB_TYPE:
        return JobStatus.FAILED.value, "simulated permanent failure", PERMANENT
    return JobStatus.SUCCEEDED.value, None, None


class _HeartbeatLoop:
    """Runs on its own thread and its own DB session/connection -- independent
    of whatever the executor is doing, per ADR 004's hard architectural
    requirement. Sets `abandoned` if a renewal is ever rejected (fenced out)."""

    def __init__(self, job_id: uuid.UUID, worker_id: str, attempt_number: int):
        self.job_id = job_id
        self.worker_id = worker_id
        self.attempt_number = attempt_number
        self.abandoned = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(settings.heartbeat_interval_seconds):
            db = SessionLocal()
            try:
                renewed = repo.heartbeat(
                    db, self.job_id, self.worker_id, self.attempt_number,
                    settings.lease_duration_seconds,
                )
                if not renewed:
                    self.abandoned.set()
                    return
            finally:
                db.close()


def process_job_message(db: Session, job_id: uuid.UUID, worker_id: str) -> str:
    """Idempotent, fencing-safe handling of one job.queued (or retry-dispatch)
    message. Returns a short outcome string for logging/testing:
      "claimed"      -- this call ran the job to a terminal-or-retry state
      "not_claimed"  -- claim failed: already running/terminal, cancelled,
                        or retry backoff not yet due (all handled by the
                        same conditional UPDATE, see app/repository/jobs.py::claim)
      "fenced_out"   -- claimed, but lost the lease mid-execution (heartbeat
                        or finalize rejected) -- result discarded, never retried
                        from here (Recovery will have already requeued or
                        failed the job under a newer attempt_number).
    """
    job = repo.claim(db, job_id, worker_id, settings.lease_duration_seconds)
    if job is None:
        return "not_claimed"

    attempt_number = job.attempt_number
    attempts_repo.insert(db, job_id, attempt_number, worker_id)

    heartbeat_loop = _HeartbeatLoop(job_id, worker_id, attempt_number)
    heartbeat_loop.start()
    try:
        outcome, error_message, classification = _run_executor(job)
    finally:
        heartbeat_loop.stop()

    if heartbeat_loop.abandoned.is_set():
        return "fenced_out"

    # Re-read to see if cancellation was requested during execution -- the
    # only checkpoint V0.3 has, same cooperative/last-checkpoint-wins model
    # as V0.2 (STATE_TRANSITIONS_V0.2.md), now fencing-conditioned as well.
    current = repo.get(db, job_id)
    if current is not None and current.cancel_requested:
        finalized = repo.finalize_attempt(
            db, job_id, worker_id, attempt_number, JobStatus.CANCELLED.value
        )
        attempts_repo.finalize(db, job_id, attempt_number, JobStatus.CANCELLED.value)
        return "claimed" if finalized is not None else "fenced_out"

    if outcome == JobStatus.SUCCEEDED.value:
        finalized = repo.finalize_attempt(db, job_id, worker_id, attempt_number, JobStatus.SUCCEEDED.value)
        attempts_repo.finalize(db, job_id, attempt_number, JobStatus.SUCCEEDED.value)
        return "claimed" if finalized is not None else "fenced_out"

    # outcome == FAILED: classify and decide retry vs permanent-fail vs DLQ.
    classification = classification or UNKNOWN
    if is_retryable(classification, attempt_number, settings.max_attempts):
        next_retry_at = compute_next_retry_at(
            attempt_number, settings.base_delay_seconds, settings.max_delay_seconds, settings.jitter_ratio
        )
        finalized = repo.finalize_attempt(
            db, job_id, worker_id, attempt_number, JobStatus.QUEUED.value,
            extra_values={"next_retry_at": next_retry_at},
        )
        attempts_repo.finalize(db, job_id, attempt_number, JobStatus.FAILED.value, error_message, classification)
        return "claimed" if finalized is not None else "fenced_out"

    # Permanent, or transient-but-exhausted: terminal FAILED + DLQ.
    finalized = repo.finalize_attempt(db, job_id, worker_id, attempt_number, JobStatus.FAILED.value)
    attempts_repo.finalize(db, job_id, attempt_number, JobStatus.FAILED.value, error_message, classification)
    if finalized is not None:
        dlq_repo.insert(
            db, job_id,
            last_attempt_number=attempt_number,
            last_error_message=error_message,
            last_error_classification=classification,
            total_attempts=attempt_number,
        )
        return "claimed"
    return "fenced_out"
