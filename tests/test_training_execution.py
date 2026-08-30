"""End-to-end V0.6 tests: real subprocess spawned via
`python -m app.training.subprocess_main`, real checkpoint files, real
SHA-256 hashing, real fencing-conditioned registration -- using the
dependency-free toy training body (app/training/toy_trainer.py) since this
environment has no CUDA GPU. See V0.6_GPU_VALIDATION.md for the separately-
validated real LoRA/QLoRA run on a real Tesla T4."""
from app.models.job import JobStatus
from app.repository import checkpoints as checkpoints_repo
from app.repository import training_metrics as metrics_repo
from app.repository import training_run_outputs as outputs_repo
from app.repository import jobs as repo
from app.schemas import JobCreate
from app.services import datasets as datasets_service
from app.services import jobs as service
from app.services import training_runs as training_runs_service
from app.services.scheduler import try_admit
from app.services.worker import process_job_message
from tests.conftest import reserve_for_claim


def _make_training_run(db_session, storage_client, max_steps=6, checkpoint_every=2):
    dataset = datasets_service.create_dataset(db_session, "toy-dataset", None)
    version = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"toy data", "u1")
    run = training_runs_service.create_training_run(
        db_session,
        job_type="training_run",
        dataset_id=dataset.id,
        dataset_version_number=version.version_number,
        training_config={
            "max_steps": max_steps,
            "checkpoint_every_n_steps": checkpoint_every,
            "learning_rate": 0.5,
            "target_value": 1.0,
        },
        code_commit="testcommit",
        container_image="test:latest",
    )
    return run


def test_real_subprocess_training_produces_final_artifact_and_lineage(db_session, storage_client):
    run = _make_training_run(db_session, storage_client, max_steps=6, checkpoint_every=2)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)

    outcome = process_job_message(db_session, run.job_id, worker_id="w1", storage_client=storage_client)

    assert outcome == "claimed"
    final_job = repo.get(db_session, run.job_id)
    assert final_job.status == JobStatus.SUCCEEDED.value

    # Real checkpoints registered at steps 2 and 4 (6 is the final step, not
    # an intermediate checkpoint -- toy_trainer only checkpoints before max_steps).
    checkpoints = checkpoints_repo.list_for_run(db_session, run.id)
    steps = sorted(c.step for c in checkpoints)
    assert steps == [2, 4]

    # Real metrics recorded for every step.
    metrics, total = metrics_repo.list_for_run(db_session, run.id, limit=100, offset=0)
    assert total == 6
    assert [m.step for m in metrics] == [1, 2, 3, 4, 5, 6]
    assert metrics[-1].loss < metrics[0].loss  # real, decreasing toy loss

    # Final artifact registered -- distinguished from checkpoints.
    output = outputs_repo.get(db_session, run.id)
    assert output is not None
    assert output.attempt_number == 1
    assert output.final_artifact_id not in {c.artifact_id for c in checkpoints}


def test_worker_kill_then_retry_resumes_from_checkpoint(db_session, storage_client):
    """The core V0.6 release-blocking test: worker dies mid-training after a
    checkpoint, V0.3 Recovery reclaims (unmodified), a retry resumes from the
    registered checkpoint (ADR 015) and completes."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.services.recovery import reclaim_stale_leases

    run = _make_training_run(db_session, storage_client, max_steps=6, checkpoint_every=2)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)

    # Manually claim (simulating the Worker's claim step) and directly invoke
    # the executor for one checkpoint, then simulate a crash by never
    # finishing -- i.e., call the real subprocess path but interrupt after
    # the first checkpoint by monkeypatching max_steps down for this "attempt".
    # Simplest faithful simulation: run process_job_message normally (it
    # completes attempt 1 in one shot, since the toy body doesn't crash on
    # its own) -- so instead we directly exercise Recovery's reclaim on a
    # manually-claimed, manually-checkpointed job to prove resume, decoupled
    # from needing a real mid-flight kill signal in this test process.
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    assert claimed is not None
    from app.repository import attempts as attempts_repo

    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")

    # Worker-A produces one real checkpoint via the real upload+registration path.
    from app.services import artifacts as artifacts_service

    checkpoint_bytes = b'{"param": 0.5, "step": 2}'
    artifact = artifacts_service.upload_artifact(
        db_session, storage_client, checkpoint_bytes, "CHECKPOINT", "worker-A",
        job_id=run.job_id, attempt_number=1,
    )
    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, 1, step=2, artifact_id=artifact.id,
        base_model_id=run.base_model_id, base_model_version_number=run.base_model_version_number,
        checkpoint_format_version=1,
    )
    assert registered is True

    # Worker-A's lease expires (it "died"); Recovery reclaims.
    db_session.execute(
        text("UPDATE jobs SET lease_expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "id": str(run.job_id)},
    )
    db_session.commit()
    reclaimed = reclaim_stale_leases(db_session)
    assert reclaimed == 1

    after_reclaim = repo.get(db_session, run.job_id)
    assert after_reclaim.status == JobStatus.QUEUED.value
    db_session.execute(
        text("UPDATE jobs SET next_retry_at = now() WHERE id = :id"), {"id": str(run.job_id)}
    )
    db_session.commit()

    # Attempt 2: real subprocess run, should discover and resume from step 2's checkpoint.
    reserve_for_claim(db_session, repo.get(db_session, run.job_id))
    outcome = process_job_message(db_session, run.job_id, worker_id="w2", storage_client=storage_client)

    assert outcome == "claimed"
    final_job = repo.get(db_session, run.job_id)
    assert final_job.status == JobStatus.SUCCEEDED.value
    assert final_job.attempt_number == 2

    from app.repository import attempt_resume_decisions as resume_decisions_repo

    decision = resume_decisions_repo.get(db_session, run.id, attempt_number=2)
    assert decision is not None
    assert decision.resumed_from_step == 2  # proves resume actually happened, not a fresh start

    # Attempt 1's checkpoint (LOST attempt's) is preserved, distinct from
    # attempt 2's new checkpoints/output -- full lineage across the retry.
    all_checkpoints = checkpoints_repo.list_for_run(db_session, run.id)
    attempt_1_checkpoints = [c for c in all_checkpoints if c.attempt_number == 1]
    attempt_2_checkpoints = [c for c in all_checkpoints if c.attempt_number == 2]
    assert len(attempt_1_checkpoints) == 1
    assert attempt_1_checkpoints[0].step == 2
    assert len(attempt_2_checkpoints) >= 1
