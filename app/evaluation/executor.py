"""Worker-side supervisor for the evaluation subprocess (ADR 014/017 -- the
evaluation analogue of app/training/executor.py). Runs INSIDE the Worker
process: resolves + downloads the exact model and dataset artifacts, spawns
the subprocess, reads its reported events, and performs every
fencing-conditioned database write on its behalf (ADR 016). The subprocess
never touches Postgres.

Every event type the subprocess can emit (result / metric / final, plus the
fail-closed checks) is wired here from day one -- the V0.6 bug where a metric
event type was silently dropped is the reason this is called out explicitly.
"""
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from app.config import settings
from app.repository import artifacts as artifacts_repo
from app.repository import datasets as datasets_repo
from app.repository import evaluation_configs as configs_repo
from app.repository import evaluation_metrics as metrics_repo
from app.repository import evaluation_results as results_repo
from app.repository import evaluation_runs as runs_repo
from app.repository import models as models_repo
from app.storage import download

logger = logging.getLogger("app")


class EvaluationOutcome:
    def __init__(self, status: str, error_message: str | None = None,
                 error_classification: str | None = None, failure_domain: str | None = None):
        self.status = status  # SUCCEEDED | FAILED
        self.error_message = error_message
        self.error_classification = error_classification
        self.failure_domain = failure_domain


def run_evaluation_attempt(
    db, storage_client, job, worker_id: str, attempt_number: int, evaluation_run, heartbeat_loop,
) -> EvaluationOutcome:
    work_dir = tempfile.mkdtemp(prefix=f"evaluation-{job.id}-{attempt_number}-")
    try:
        config = configs_repo.get(db, evaluation_run.evaluation_config_id)

        # Resolve + download the EXACT registered artifacts (ADR 018). Fail
        # closed if the model/dataset artifact is missing or not UPLOADED
        # (FAILURE_SCENARIOS_V0.7.md #9, #10).
        model_prep = _prepare_artifact(
            db, storage_client, work_dir, "model",
            models_repo.get_version(db, evaluation_run.model_id, evaluation_run.model_version_number),
        )
        if model_prep is None:
            return _fail(db, evaluation_run, job, worker_id, attempt_number,
                         "model artifact unavailable or not UPLOADED", "permanent", "DATA")
        dataset_prep = _prepare_artifact(
            db, storage_client, work_dir, "dataset",
            datasets_repo.get_version(db, evaluation_run.dataset_id, evaluation_run.dataset_version_number),
        )
        if dataset_prep is None:
            return _fail(db, evaluation_run, job, worker_id, attempt_number,
                         "dataset artifact unavailable or not UPLOADED", "permanent", "DATA")

        metric_definitions = (config.metric_definitions or {}) if config else {}
        context = {
            "evaluation_run_id": str(evaluation_run.id),
            "attempt_number": attempt_number,
            "work_dir": work_dir,
            "required_gpu": (job.config or {}).get("resources", {}).get("gpu", 0),
            "required_memory_mb": (job.config or {}).get("resources", {}).get("memory_mb", 0),
            "evaluator_entrypoint": metric_definitions.get("evaluator_entrypoint", "app.evaluation.toy_evaluator"),
            "task_type": config.task_type if config else "text",
            "batch_size": config.batch_size if config else 1,
            "max_examples": config.max_examples if config else None,
            "max_sequence_length": config.max_sequence_length if config else None,
            "random_seed": config.random_seed if config else None,
            "split": metric_definitions.get("split", "all"),
            "model_path": model_prep[0], "model_expected_hash": model_prep[1],
            "dataset_path": dataset_prep[0], "dataset_expected_hash": dataset_prep[1],
        }

        # Fenced transition to RUNNING. If this fails we were already fenced.
        if not runs_repo.mark_status(db, evaluation_run.id, job.id, worker_id, attempt_number,
                                     "RUNNING", set_completed=False):
            return EvaluationOutcome(status="FAILED", error_message="fenced_out",
                                     error_classification="transient", failure_domain="INFRASTRUCTURE")

        context_path = os.path.join(work_dir, "context.json")
        with open(context_path, "w") as f:
            json.dump(context, f)

        process = subprocess.Popen(
            [sys.executable, "-m", "app.evaluation.subprocess_main", context_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        last_error = None
        produced_final = False
        for line in process.stdout:
            if heartbeat_loop.abandoned.is_set():
                _terminate(process)
                return EvaluationOutcome(status="FAILED", error_message="fenced_out",
                                         error_classification="transient", failure_domain="INFRASTRUCTURE")
            event = _parse_event(line)
            if event is None:
                continue
            outcome = _handle_event(db, job, worker_id, evaluation_run, attempt_number, event)
            if outcome == "final":
                produced_final = True
            elif outcome == "gpu_failed":
                last_error = ("cuda_unavailable_or_insufficient", "transient", "INFRASTRUCTURE")
            elif outcome == "artifact_failed":
                last_error = (f"artifact identity check failed: {event.get('artifact')}", "permanent", "DATA")
            elif outcome == "evaluation_error":
                last_error = (event.get("message", "evaluation error"), "permanent", "EVALUATION")

        process.wait()

        if heartbeat_loop.abandoned.is_set():
            return EvaluationOutcome(status="FAILED", error_message="fenced_out",
                                     error_classification="transient", failure_domain="INFRASTRUCTURE")
        if produced_final:
            if not runs_repo.mark_status(db, evaluation_run.id, job.id, worker_id, attempt_number,
                                         "SUCCEEDED", set_completed=True):
                return EvaluationOutcome(status="FAILED", error_message="fenced_out",
                                         error_classification="transient", failure_domain="INFRASTRUCTURE")
            return EvaluationOutcome(status="SUCCEEDED")
        if last_error:
            message, classification, domain = last_error
            runs_repo.mark_status(db, evaluation_run.id, job.id, worker_id, attempt_number,
                                  "FAILED", set_completed=False)
            return EvaluationOutcome(status="FAILED", error_message=message,
                                     error_classification=classification, failure_domain=domain)
        runs_repo.mark_status(db, evaluation_run.id, job.id, worker_id, attempt_number,
                              "FAILED", set_completed=False)
        return EvaluationOutcome(status="FAILED",
                                 error_message="subprocess exited without producing a final evaluation",
                                 error_classification="unknown", failure_domain="INFRASTRUCTURE")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _fail(db, evaluation_run, job, worker_id, attempt_number, message, classification, domain):
    runs_repo.mark_status(db, evaluation_run.id, job.id, worker_id, attempt_number,
                          "FAILED", set_completed=False)
    return EvaluationOutcome(status="FAILED", error_message=message,
                             error_classification=classification, failure_domain=domain)


def _prepare_artifact(db, storage_client, work_dir, kind, version):
    """Returns (local_path, expected_content_hash) or None if unavailable."""
    if version is None:
        return None
    artifact = artifacts_repo.get(db, version.artifact_id)
    if artifact is None or artifact.status != "UPLOADED":
        return None
    try:
        data = download(storage_client, artifact.storage_key)
    except Exception:  # noqa: BLE001 -- storage unavailable/missing object: fail closed
        return None
    local_path = os.path.join(work_dir, f"{kind}.bin")
    with open(local_path, "wb") as f:
        f.write(data)
    return local_path, artifact.content_hash


def _parse_event(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _handle_event(db, job, worker_id, evaluation_run, attempt_number, event: dict) -> str | None:
    event_type = event.get("event")
    if event_type == "gpu_check_failed":
        logger.info("evaluation_gpu_check_failed", extra={"job_id": str(job.id), "reason": event.get("reason")})
        return "gpu_failed"
    if event_type == "artifact_check_failed":
        logger.info("evaluation_artifact_check_failed",
                    extra={"job_id": str(job.id), "artifact": event.get("artifact")})
        return "artifact_failed"
    if event_type == "evaluation_error":
        logger.info("evaluation_error", extra={"job_id": str(job.id), "message": event.get("message")})
        return "evaluation_error"
    if event_type == "result":
        results_repo.record(
            db, job.id, worker_id, evaluation_run.id, attempt_number,
            example_id=str(event["example_id"]),
            prediction=_as_text(event.get("prediction")),
            expected_output=_as_text(event.get("expected_output")),
            score=event.get("score"),
            latency_ms=event.get("latency_ms"),
        )
        return "result_recorded"
    if event_type == "metric":
        metrics_repo.record(
            db, job.id, worker_id, evaluation_run.id, attempt_number,
            metric_name=event["metric_name"],
            metric_value=event["metric_value"],
            split=event.get("split", "all"),
            sample_count=event.get("sample_count", 0),
        )
        return "metric_recorded"
    if event_type == "final":
        return "final"
    return None


def _as_text(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


def _terminate(process: subprocess.Popen) -> None:
    """ADR 014: SIGTERM, wait, then SIGKILL."""
    process.send_signal(signal.SIGTERM)
    deadline = time.time() + settings.training_termination_grace_seconds
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        process.kill()
        process.wait()
