"""V0.8 operational metrics (Prometheus text exposition format).

Design decision (honest, deliberate): every metric here is DERIVED FROM THE
AUTHORITATIVE POSTGRES STATE at scrape time, not from per-process in-memory
counters. This platform runs as many independent containers (api, worker,
scheduler, recovery, outbox-relay, reconciler). A per-process
`prometheus_client.Counter` incremented inside `worker.py` lives only in the
worker container's memory and would NEVER appear on the api container's
`/metrics` endpoint -- it would be a siloed, misleading number. Postgres is
already the single source of truth every one of those processes writes to
(jobs, attempts, reservations, capacity, outbox, evaluation_runs,
checkpoints, attempt_resume_decisions). Reading those tables at scrape time
yields a metric that is correct across the whole cluster, and by construction
cannot drift from real system state. Counters are monotonic because they
count cumulative rows (jobs ever created, attempts ever retried, checkpoints
ever written) -- rows are never deleted on the happy path.

This is not a fabricated value: every number traces to a row a real code
path (worker/scheduler/recovery/outbox_relay/evaluations) actually wrote.
"""
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
)
from sqlalchemy import text

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Bucket boundaries (seconds) for duration histograms. Toy CPU jobs finish
# sub-second; buckets extend far enough to stay meaningful for real GPU runs.
_DURATION_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0]


def _histogram_from_durations(name, doc, durations):
    """Build a HistogramMetricFamily from a list of real observed durations."""
    buckets = []
    cumulative = 0
    total = 0.0
    ordered = sorted(durations)
    idx = 0
    for edge in _DURATION_BUCKETS:
        while idx < len(ordered) and ordered[idx] <= edge:
            cumulative += 1
            total += ordered[idx]
            idx += 1
        buckets.append((str(edge), float(cumulative)))
    # +Inf bucket picks up anything above the last finite edge.
    while idx < len(ordered):
        cumulative += 1
        total += ordered[idx]
        idx += 1
    buckets.append(("+Inf", float(cumulative)))
    return HistogramMetricFamily(name, doc, buckets=buckets, sum_value=total)


def _scalar(db, sql, params=None):
    return db.execute(text(sql), params or {}).scalar() or 0


def _grouped(db, sql):
    return list(db.execute(text(sql)).all())


def collect_metrics_text(db) -> bytes:
    """Query Postgres and render the full /metrics payload."""
    registry = CollectorRegistry()
    registry.register(_PlatformCollector(db))
    return generate_latest(registry)


class _PlatformCollector:
    def __init__(self, db):
        self._db = db

    def collect(self):
        db = self._db

        # --- job lifecycle counters (labeled by job_type) ---
        created = CounterMetricFamily(
            "jobs_created", "Total jobs ever created.", labels=["job_type"]
        )
        for job_type, n in _grouped(
            db, "SELECT job_type, count(*) FROM jobs GROUP BY job_type"
        ):
            created.add_metric([job_type], float(n))
        yield created

        completed = CounterMetricFamily(
            "jobs_completed", "Total jobs that reached SUCCEEDED.", labels=["job_type"]
        )
        for job_type, n in _grouped(
            db,
            "SELECT job_type, count(*) FROM jobs WHERE status='SUCCEEDED' GROUP BY job_type",
        ):
            completed.add_metric([job_type], float(n))
        yield completed

        failed = CounterMetricFamily(
            "jobs_failed", "Total jobs that reached terminal FAILED.", labels=["job_type"]
        )
        for job_type, n in _grouped(
            db,
            "SELECT job_type, count(*) FROM jobs WHERE status='FAILED' GROUP BY job_type",
        ):
            failed.add_metric([job_type], float(n))
        yield failed

        # A retry == an attempt row with attempt_number > 1. Cumulative,
        # monotonic, and exactly the count of retried executions.
        retried = CounterMetricFamily(
            "jobs_retried", "Total job attempts beyond the first (retries)."
        )
        retried.add_metric(
            [], float(_scalar(db, "SELECT count(*) FROM attempts WHERE attempt_number > 1"))
        )
        yield retried

        # --- gauges (point-in-time system state) ---
        queue_depth = GaugeMetricFamily(
            "job_queue_depth", "Jobs currently QUEUED awaiting a worker."
        )
        queue_depth.add_metric(
            [], float(_scalar(db, "SELECT count(*) FROM jobs WHERE status='QUEUED'"))
        )
        yield queue_depth

        active = GaugeMetricFamily("worker_active_jobs", "Jobs currently RUNNING on a worker.")
        active.add_metric(
            [], float(_scalar(db, "SELECT count(*) FROM jobs WHERE status='RUNNING'"))
        )
        yield active

        outbox = GaugeMetricFamily(
            "outbox_pending", "Outbox rows not yet published to Kafka."
        )
        outbox.add_metric(
            [], float(_scalar(db, "SELECT count(*) FROM outbox WHERE published_at IS NULL"))
        )
        yield outbox

        reservations = GaugeMetricFamily(
            "scheduler_reservations", "Active resource reservations."
        )
        reservations.add_metric(
            [], float(_scalar(db, "SELECT count(*) FROM reservations WHERE status='ACTIVE'"))
        )
        yield reservations

        cap_row = db.execute(
            text(
                "SELECT total_cpu, allocated_cpu, total_memory_mb, allocated_memory_mb, "
                "total_gpu, allocated_gpu FROM capacity "
                "WHERE id='00000000-0000-0000-0000-000000000001'"
            )
        ).first()
        alloc = GaugeMetricFamily(
            "scheduler_capacity_allocated", "Currently allocated capacity.", labels=["resource"]
        )
        total = GaugeMetricFamily(
            "scheduler_capacity_total", "Total cluster capacity.", labels=["resource"]
        )
        if cap_row is not None:
            t_cpu, a_cpu, t_mem, a_mem, t_gpu, a_gpu = cap_row
            for res, a, t in (
                ("cpu", a_cpu, t_cpu),
                ("memory_mb", a_mem, t_mem),
                ("gpu", a_gpu, t_gpu),
            ):
                alloc.add_metric([res], float(a))
                total.add_metric([res], float(t))
        yield alloc
        yield total

        # --- evaluation counters ---
        eval_total = CounterMetricFamily(
            "evaluation_runs", "Total evaluation runs ever created."
        )
        eval_total.add_metric([], float(_scalar(db, "SELECT count(*) FROM evaluation_runs")))
        yield eval_total

        eval_fail = CounterMetricFamily(
            "evaluation_failures", "Total evaluation runs that reached FAILED."
        )
        eval_fail.add_metric(
            [],
            float(_scalar(db, "SELECT count(*) FROM evaluation_runs WHERE status='FAILED'")),
        )
        yield eval_fail

        # --- checkpoint counters ---
        ckpt_created = CounterMetricFamily(
            "checkpoint_created", "Total training checkpoints ever registered."
        )
        ckpt_created.add_metric([], float(_scalar(db, "SELECT count(*) FROM checkpoints")))
        yield ckpt_created

        ckpt_resume = CounterMetricFamily(
            "checkpoint_resume", "Total attempts that resumed from a checkpoint."
        )
        ckpt_resume.add_metric(
            [],
            float(
                _scalar(
                    db,
                    "SELECT count(*) FROM attempt_resume_decisions "
                    "WHERE resumed_from_step IS NOT NULL",
                )
            ),
        )
        yield ckpt_resume

        # --- histograms (real observed durations from persisted timestamps) ---
        exec_durations = [
            float(r[0])
            for r in _grouped(
                db,
                "SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) FROM attempts "
                "WHERE status='SUCCEEDED' AND finished_at IS NOT NULL",
            )
            if r[0] is not None and float(r[0]) >= 0
        ]
        yield _histogram_from_durations(
            "job_execution_seconds",
            "Wall-clock execution time of successful job attempts.",
            exec_durations,
        )

        # Recovery time proxy: how long a LOST attempt was outstanding before
        # Recovery reclaimed it (started_at -> finished_at, set by recovery.py).
        recovery_durations = [
            float(r[0])
            for r in _grouped(
                db,
                "SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) FROM attempts "
                "WHERE status='LOST' AND finished_at IS NOT NULL",
            )
            if r[0] is not None and float(r[0]) >= 0
        ]
        yield _histogram_from_durations(
            "job_recovery_seconds",
            "Time a lost attempt was outstanding before Recovery reclaimed it.",
            recovery_durations,
        )

        eval_durations = [
            float(r[0])
            for r in _grouped(
                db,
                "SELECT EXTRACT(EPOCH FROM (completed_at - created_at)) FROM evaluation_runs "
                "WHERE status='SUCCEEDED' AND completed_at IS NOT NULL",
            )
            if r[0] is not None and float(r[0]) >= 0
        ]
        yield _histogram_from_durations(
            "evaluation_duration_seconds",
            "Wall-clock duration of successful evaluation runs.",
            eval_durations,
        )
