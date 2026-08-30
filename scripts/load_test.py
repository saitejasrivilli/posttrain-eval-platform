#!/usr/bin/env python3
"""V0.8 load test (single, reproducible -- NOT a benchmark suite).

Submits N jobs across C concurrent submitter threads against the REAL running
docker-compose stack (real HTTP -> real outbox -> real Kafka -> real Scheduler
-> real Worker -> real Postgres), waits for every job to reach a terminal
state, then computes REAL measured numbers from the persisted timestamps:
  - throughput (jobs/sec)
  - queue wait p50/p95 (created_at -> claimed_at)
  - execution time p50/p95 (attempt started_at -> finished_at)
  - retry rate (attempts beyond the first / jobs)
and asserts the resource-conservation invariant: allocated capacity returns
to 0 after all jobs finish (ADR 007/009).

Numbers are written to benchmark/results/v0.8_load_test.json. Every value
traces to a row a real code path wrote -- nothing is estimated.

Usage:
  python scripts/load_test.py --n 100 --concurrency 10 \
      --base-url http://localhost:8000 \
      --db postgresql://postgres:postgres@localhost:55432/postgres
"""
import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import psycopg2


def _pct(values, q):
    if not values:
        return None
    return round(statistics.quantiles(values, n=100)[q - 1] if len(values) > 1 else values[0], 6)


def submit_job(base_url, job_type):
    r = httpx.post(f"{base_url}/v1/jobs", json={"job_type": job_type}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--job-type", default="sft")
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    ap.add_argument(
        "--db",
        default=os.environ.get(
            "LOADTEST_DB_URL", "postgresql://postgres:postgres@localhost:55432/postgres"
        ),
    )
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default="benchmark/results/v0.8_load_test.json")
    args = ap.parse_args()

    conn = psycopg2.connect(args.db)
    conn.autocommit = True

    def capacity_allocated():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT allocated_cpu, allocated_memory_mb, allocated_gpu FROM capacity "
                "WHERE id='00000000-0000-0000-0000-000000000001'"
            )
            return cur.fetchone()

    alloc_before = capacity_allocated()
    print(f"capacity allocated before: cpu={alloc_before[0]} mem={alloc_before[1]} gpu={alloc_before[2]}")

    print(f"submitting {args.n} jobs with {args.concurrency} concurrent submitters...")
    wall_start = time.time()
    job_ids = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(submit_job, args.base_url, args.job_type) for _ in range(args.n)]
        for f in as_completed(futures):
            job_ids.append(f.result())
    submit_elapsed = time.time() - wall_start
    print(f"submitted {len(job_ids)} jobs in {submit_elapsed:.2f}s")

    # Poll until all jobs terminal.
    terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
    deadline = time.time() + args.timeout
    id_tuple = tuple(job_ids)
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM jobs WHERE id IN %s AND status = ANY(%s)",
                (id_tuple, list(terminal)),
            )
            done = cur.fetchone()[0]
        if done >= len(job_ids):
            break
        if time.time() > deadline:
            print(f"TIMEOUT: only {done}/{len(job_ids)} terminal", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)

    # Pull real timestamps for measured metrics.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, "
            "  EXTRACT(EPOCH FROM (claimed_at - created_at)), "
            "  created_at, updated_at "
            "FROM jobs WHERE id IN %s",
            (id_tuple,),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT job_id, attempt_number, "
            "  EXTRACT(EPOCH FROM (finished_at - started_at)) "
            "FROM attempts WHERE job_id IN %s",
            (id_tuple,),
        )
        attempt_rows = cur.fetchall()
        cur.execute(
            "SELECT min(created_at), max(updated_at) FROM jobs WHERE id IN %s", (id_tuple,)
        )
        first_created, last_updated = cur.fetchone()

    queue_waits = sorted(float(r[1]) for r in rows if r[1] is not None and float(r[1]) >= 0)
    exec_times = sorted(float(a[2]) for a in attempt_rows if a[2] is not None and float(a[2]) >= 0)
    total_attempts = len(attempt_rows)
    retries = sum(1 for a in attempt_rows if a[1] > 1)
    succeeded = sum(1 for r in rows if r[0] == "SUCCEEDED")
    failed = sum(1 for r in rows if r[0] == "FAILED")

    wall_span = (last_updated - first_created).total_seconds()
    throughput = round(len(job_ids) / wall_span, 4) if wall_span > 0 else None

    alloc_after = capacity_allocated()
    conservation_ok = alloc_after == (0, 0, 0)

    result = {
        "config": {
            "n_jobs": args.n,
            "concurrency": args.concurrency,
            "job_type": args.job_type,
            "base_url": args.base_url,
        },
        "measured": {
            "jobs_submitted": len(job_ids),
            "jobs_succeeded": succeeded,
            "jobs_failed": failed,
            "submit_elapsed_seconds": round(submit_elapsed, 4),
            "processing_wall_span_seconds": round(wall_span, 4),
            "throughput_jobs_per_sec": throughput,
            "queue_wait_seconds": {
                "p50": _pct(queue_waits, 50),
                "p95": _pct(queue_waits, 95),
                "max": round(max(queue_waits), 6) if queue_waits else None,
            },
            "execution_seconds": {
                "p50": _pct(exec_times, 50),
                "p95": _pct(exec_times, 95),
                "max": round(max(exec_times), 6) if exec_times else None,
            },
            "total_attempts": total_attempts,
            "retries": retries,
            "retry_rate": round(retries / len(job_ids), 4) if job_ids else None,
        },
        "resource_conservation": {
            "allocated_before": {"cpu": alloc_before[0], "memory_mb": alloc_before[1], "gpu": alloc_before[2]},
            "allocated_after": {"cpu": alloc_after[0], "memory_mb": alloc_after[1], "gpu": alloc_after[2]},
            "returns_to_zero": conservation_ok,
        },
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result["measured"], indent=2))
    print(f"resource conservation returns_to_zero={conservation_ok}")
    print(f"written to {args.out}")

    if not conservation_ok:
        print("INVARIANT VIOLATED: allocated capacity did not return to 0", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
