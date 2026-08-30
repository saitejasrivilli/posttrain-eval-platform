"""Release-blocking test tier for V0.6 (same tier as V0.3's split-brain test /
V0.4's no-overcommit test / V0.5's artifact-consistency tests): a stale
worker must never be able to register a TRUSTED checkpoint or final output,
even if its artifact bytes uploaded fine (ADR 016's second fencing layer),
and find_resume_checkpoint() must reject any checkpoint failing its 6
compatibility rules (ADR 015)."""
import uuid

from app.config import settings
from app.repository import attempts as attempts_repo
from app.repository import checkpoints as checkpoints_repo
from app.repository import jobs as repo
from app.repository import training_run_outputs as outputs_repo
from app.services import artifacts as artifacts_service
from app.services import datasets as datasets_service
from app.services import training_runs as training_runs_service
from app.training.checkpoint_discovery import find_resume_checkpoint
from tests.conftest import reserve_for_claim


def _make_run(db_session, storage_client):
    dataset = datasets_service.create_dataset(db_session, "toy-dataset", None)
    version = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"toy data", "u1")
    return training_runs_service.create_training_run(
        db_session,
        job_type="training_run",
        dataset_id=dataset.id,
        dataset_version_number=version.version_number,
        training_config={"max_steps": 6, "checkpoint_every_n_steps": 2, "learning_rate": 0.5, "target_value": 1.0},
        code_commit="testcommit",
        container_image="test:latest",
    )


def _upload(db_session, storage_client, run, worker_id, attempt_number, body=b'{"param": 0.5, "step": 2}'):
    return artifacts_service.upload_artifact(
        db_session, storage_client, body, "CHECKPOINT", worker_id, job_id=run.job_id, attempt_number=attempt_number,
    )


def test_stale_worker_cannot_register_checkpoint(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    assert claimed is not None
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")

    artifact = _upload(db_session, storage_client, run, "worker-A", 1)

    # Worker-A gets fenced out (finalized to FAILED) before it registers.
    finalized = repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "FAILED")
    assert finalized is not None

    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, 1, step=2, artifact_id=artifact.id,
        base_model_id=run.base_model_id, base_model_version_number=run.base_model_version_number,
        checkpoint_format_version=1,
    )
    assert registered is False
    assert checkpoints_repo.list_for_run(db_session, run.id) == []


def test_stale_worker_cannot_finalize_output(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    assert claimed is not None
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")

    artifact = artifacts_service.upload_artifact(
        db_session, storage_client, b'{"final": true}', "MODEL", "worker-A", job_id=run.job_id, attempt_number=1,
    )

    finalized = repo.finalize_attempt(db_session, run.job_id, "worker-A", 1, "FAILED")
    assert finalized is not None

    registered = outputs_repo.register(db_session, run.job_id, "worker-A", run.id, 1, artifact.id)
    assert registered is False
    assert outputs_repo.get(db_session, run.id) is None


def test_wrong_attempt_number_cannot_register_checkpoint(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    assert claimed is not None
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")

    artifact = _upload(db_session, storage_client, run, "worker-A", 1)

    # Same worker, still RUNNING, but claims a stale attempt_number (e.g. its
    # own retried/superseded write racing after a reclaim+reclaim bumped the
    # live attempt forward) -- must still be rejected.
    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, attempt_number=99, step=2, artifact_id=artifact.id,
        base_model_id=run.base_model_id, base_model_version_number=run.base_model_version_number,
        checkpoint_format_version=1,
    )
    assert registered is False


def test_hash_mismatch_rejected(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")
    artifact = _upload(db_session, storage_client, run, "worker-A", 1)
    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, 1, step=2, artifact_id=artifact.id,
        base_model_id=run.base_model_id, base_model_version_number=run.base_model_version_number,
        checkpoint_format_version=1,
    )
    assert registered is True

    # Corrupt the stored object after the fact -- hash on record no longer
    # matches actual bytes in storage.
    storage_client.put_object(Bucket=settings.minio_bucket, Key=artifact.storage_key, Body=b"corrupted")

    found = find_resume_checkpoint(db_session, run.id, storage_client)
    assert found is None


def test_base_model_mismatch_rejected(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")
    artifact = _upload(db_session, storage_client, run, "worker-A", 1)
    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, 1, step=2, artifact_id=artifact.id,
        base_model_id=uuid.uuid4(), base_model_version_number=1,
        checkpoint_format_version=1,
    )
    assert registered is True

    found = find_resume_checkpoint(db_session, run.id, storage_client)
    assert found is None


def test_unsupported_format_version_rejected(db_session, storage_client):
    run = _make_run(db_session, storage_client)
    job = repo.get(db_session, run.job_id)
    reserve_for_claim(db_session, job)
    claimed = repo.claim(db_session, run.job_id, "worker-A", lease_duration_seconds=30)
    attempts_repo.insert(db_session, run.job_id, 1, "worker-A")
    artifact = _upload(db_session, storage_client, run, "worker-A", 1)
    registered = checkpoints_repo.register(
        db_session, run.job_id, "worker-A", run.id, 1, step=2, artifact_id=artifact.id,
        base_model_id=run.base_model_id, base_model_version_number=run.base_model_version_number,
        checkpoint_format_version=999,
    )
    assert registered is True

    found = find_resume_checkpoint(db_session, run.id, storage_client)
    assert found is None
