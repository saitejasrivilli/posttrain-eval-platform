"""Worker-side supervisor for the training subprocess (ADR 014). Runs
INSIDE the Worker process -- spawns the subprocess, reads its reported
events, and performs every fencing-conditioned database write on its
behalf (ADR 016). The subprocess itself never touches Postgres."""
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from app.config import settings
from app.repository import artifacts as artifacts_repo
from app.repository import attempt_resume_decisions as resume_decisions_repo
from app.repository import checkpoints as checkpoints_repo
from app.repository import training_metrics as metrics_repo
from app.repository import training_run_outputs as outputs_repo
from app.services import artifacts as artifacts_service
from app.training.checkpoint_discovery import SUPPORTED_CHECKPOINT_FORMAT_VERSION, find_resume_checkpoint

logger = logging.getLogger("app")


class TrainingOutcome:
    def __init__(self, status: str, error_message: str | None = None,
                 error_classification: str | None = None, failure_domain: str | None = None):
        self.status = status  # SUCCEEDED | FAILED
        self.error_message = error_message
        self.error_classification = error_classification
        self.failure_domain = failure_domain


def run_training_attempt(
    db, storage_client, job, worker_id: str, attempt_number: int, training_run, heartbeat_loop,
) -> TrainingOutcome:
    work_dir = tempfile.mkdtemp(prefix=f"training-{job.id}-{attempt_number}-")
    try:
        resumed_from = _prepare_resume(db, storage_client, training_run, work_dir)

        context = {
            "training_run_id": str(training_run.id),
            "attempt_number": attempt_number,
            "work_dir": work_dir,
            "required_gpu": (job.config or {}).get("resources", {}).get("gpu", 0),
            "required_memory_mb": (job.config or {}).get("resources", {}).get("memory_mb", 0),
            "training_entrypoint": training_run.training_config.get("training_entrypoint", "app.training.toy_trainer"),
            "resume_from": resumed_from,
            **{k: v for k, v in training_run.training_config.items() if k not in ("training_entrypoint",)},
        }
        resume_decisions_repo.record(
            db, job.id, worker_id, training_run.id, attempt_number,
            resumed_from_step=resumed_from["step"] if resumed_from else None,
        )

        context_path = os.path.join(work_dir, "context.json")
        with open(context_path, "w") as f:
            json.dump(context, f)

        process = subprocess.Popen(
            [sys.executable, "-m", "app.training.subprocess_main", context_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        last_error = None
        produced_final = False
        for line in process.stdout:
            if heartbeat_loop.abandoned.is_set():
                _terminate(process)
                return TrainingOutcome(status="FAILED", error_message="fenced_out",
                                        error_classification="transient", failure_domain="INFRASTRUCTURE")
            event = _parse_event(line)
            if event is None:
                continue
            outcome = _handle_event(
                db, storage_client, job, worker_id, training_run, attempt_number, event
            )
            if outcome == "final_registered":
                produced_final = True
            elif outcome == "gpu_failed":
                last_error = ("cuda_unavailable_or_insufficient", "transient", "INFRASTRUCTURE")
            elif outcome == "training_error":
                last_error = (event.get("message", "training error"), "permanent", "TRAINING")

        process.wait()

        if heartbeat_loop.abandoned.is_set():
            return TrainingOutcome(status="FAILED", error_message="fenced_out",
                                    error_classification="transient", failure_domain="INFRASTRUCTURE")
        if produced_final:
            return TrainingOutcome(status="SUCCEEDED")
        if last_error:
            message, classification, domain = last_error
            return TrainingOutcome(status="FAILED", error_message=message,
                                    error_classification=classification, failure_domain=domain)
        return TrainingOutcome(status="FAILED", error_message="subprocess exited without producing a final artifact",
                                error_classification="unknown", failure_domain="INFRASTRUCTURE")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _prepare_resume(db, storage_client, training_run, work_dir):
    checkpoint = find_resume_checkpoint(db, training_run.id, storage_client, SUPPORTED_CHECKPOINT_FORMAT_VERSION)
    if checkpoint is None:
        return None
    artifact = artifacts_repo.get(db, checkpoint.artifact_id)
    from app.storage import download

    data = download(storage_client, artifact.storage_key)
    local_path = os.path.join(work_dir, "resume_checkpoint.json")
    with open(local_path, "wb") as f:
        f.write(data)
    return {"local_path": local_path, "step": checkpoint.step}


def _parse_event(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _handle_event(db, storage_client, job, worker_id, training_run, attempt_number, event: dict) -> str | None:
    event_type = event.get("event")
    if event_type == "gpu_check_failed":
        logger.info("training_gpu_check_failed", extra={"job_id": str(job.id), "reason": event.get("reason")})
        return "gpu_failed"
    if event_type == "training_error":
        logger.info("training_error", extra={"job_id": str(job.id), "message": event.get("message")})
        return "training_error"
    if event_type == "metric":
        metrics_repo.record(
            db, job.id, worker_id, training_run.id, attempt_number, event["step"],
            loss=event.get("loss"), learning_rate=event.get("learning_rate"),
            gpu_memory_allocated_mb=event.get("gpu_memory_allocated_mb"),
        )
        return "metric_recorded"
    if event_type == "checkpoint":
        _upload_and_register_checkpoint(db, storage_client, job, worker_id, training_run, attempt_number, event)
        return "checkpoint_registered"
    if event_type == "final":
        _upload_and_register_final(db, storage_client, job, worker_id, training_run, attempt_number, event)
        return "final_registered"
    return None


def _upload_and_register_checkpoint(db, storage_client, job, worker_id, training_run, attempt_number, event):
    with open(event["local_path"], "rb") as f:
        data = f.read()
    artifact = artifacts_service.upload_artifact(
        db, storage_client, data, "CHECKPOINT", uploader_id=worker_id,
        job_id=job.id, attempt_number=attempt_number,
    )
    if artifact.status != "UPLOADED":
        return
    checkpoints_repo.register(
        db, job.id, worker_id, training_run.id, attempt_number, event["step"], artifact.id,
        base_model_id=training_run.base_model_id,
        base_model_version_number=training_run.base_model_version_number,
        checkpoint_format_version=SUPPORTED_CHECKPOINT_FORMAT_VERSION,
    )


def _upload_and_register_final(db, storage_client, job, worker_id, training_run, attempt_number, event):
    with open(event["local_path"], "rb") as f:
        data = f.read()
    artifact = artifacts_service.upload_artifact(
        db, storage_client, data, "MODEL", uploader_id=worker_id,
        job_id=job.id, attempt_number=attempt_number,
    )
    if artifact.status != "UPLOADED":
        return
    outputs_repo.register(db, job.id, worker_id, training_run.id, attempt_number, artifact.id)


def _terminate(process: subprocess.Popen) -> None:
    """ADR 014: SIGTERM, wait, then SIGKILL. Never assumes graceful save."""
    process.send_signal(signal.SIGTERM)
    deadline = time.time() + settings.training_termination_grace_seconds
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        process.kill()
        process.wait()
