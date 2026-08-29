from app.services import artifacts as artifacts_service
from app.services import datasets as datasets_service
from app.services import lineage as lineage_service
from app.services import models as models_service
from app.services import training_runs as training_runs_service


def _setup_dataset(db_session, storage_client):
    dataset = datasets_service.create_dataset(db_session, "lineage-test-dataset", None)
    version = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"data", "u1")
    return dataset, version


def test_training_run_references_existing_job_pipeline_unmodified(db_session, storage_client):
    """ARCHITECTURE_V0.5.md: a TrainingRun creates a job via the existing,
    unmodified V0.2 job-creation path -- job proceeds through the normal
    QUEUED lifecycle."""
    dataset, version = _setup_dataset(db_session, storage_client)

    run = training_runs_service.create_training_run(
        db_session,
        job_type="sft",
        dataset_id=dataset.id,
        dataset_version_number=version.version_number,
        training_config={"learning_rate": 0.0001, "epochs": 3},
        code_commit="91feabc",
        container_image="posttrain:latest",
        random_seed=42,
    )

    from app.repository import jobs as jobs_repo

    job = jobs_repo.get(db_session, run.job_id)
    assert job is not None
    assert job.status == "QUEUED"  # normal V0.2 auto-queue behavior, untouched


def test_full_lineage_chain_end_to_end(db_session, storage_client):
    """The central payoff of V0.5: answer "how was Model vN produced" via
    the fixed FK chain (ADR 012/LINEAGE_MODEL_V0.5.md)."""
    dataset, dataset_version = _setup_dataset(db_session, storage_client)

    run = training_runs_service.create_training_run(
        db_session,
        job_type="sft",
        dataset_id=dataset.id,
        dataset_version_number=dataset_version.version_number,
        training_config={"learning_rate": 0.0002},
        code_commit="91feabc",
        container_image="posttrain:latest",
        random_seed=7,
    )

    # Simulate the training job succeeding and its execution body uploading
    # an artifact (ARCHITECTURE_V0.5.md -- the execution body's job, not the
    # generic Worker's).
    artifact = artifacts_service.upload_artifact(
        db_session, storage_client, b"model weights v1", "MODEL", "trainer", job_id=run.job_id, attempt_number=1
    )

    model = models_service.create_model(db_session, "lineage-test-model", None)
    model_version = models_service.register_version(db_session, model.id, artifact.id, run.id)

    lineage = lineage_service.get_lineage(db_session, model.id, model_version.version_number)

    assert lineage is not None
    assert lineage["artifact"].id == artifact.id
    assert lineage["training_run"].id == run.id
    assert lineage["training_run"].code_commit == "91feabc"
    assert lineage["training_run"].training_config == {"learning_rate": 0.0002}
    assert lineage["dataset"].id == dataset.id
    assert lineage["dataset_version"].version_number == dataset_version.version_number
    assert lineage["job"].id == run.job_id


def test_base_model_lineage_one_level(db_session, storage_client):
    """A model version can be a fine-tune of another model version -- one
    more FK, not a graph traversal (ADR 012)."""
    dataset, dataset_version = _setup_dataset(db_session, storage_client)

    base_artifact = artifacts_service.upload_artifact(db_session, storage_client, b"base model", "MODEL", "u1")
    base_model = models_service.create_model(db_session, "base-model", None)
    base_version = models_service.register_version(db_session, base_model.id, base_artifact.id, None)

    run = training_runs_service.create_training_run(
        db_session,
        job_type="sft",
        dataset_id=dataset.id,
        dataset_version_number=dataset_version.version_number,
        training_config={},
        code_commit="abc",
        container_image="img",
        base_model_id=base_model.id,
        base_model_version_number=base_version.version_number,
    )

    fine_tuned_artifact = artifacts_service.upload_artifact(
        db_session, storage_client, b"fine-tuned weights", "MODEL", "trainer"
    )
    fine_tuned_model = models_service.create_model(db_session, "fine-tuned-model", None)
    fine_tuned_version = models_service.register_version(
        db_session, fine_tuned_model.id, fine_tuned_artifact.id, run.id
    )

    lineage = lineage_service.get_lineage(db_session, fine_tuned_model.id, fine_tuned_version.version_number)

    assert lineage["base_model_version"].model_id == base_model.id
    assert lineage["base_model_version"].version_number == base_version.version_number


def test_training_run_immutable_no_update_path(db_session, storage_client):
    """STATE_TRANSITIONS_V0.5.md #3: no code path updates a TrainingRun's
    columns after creation."""
    import inspect

    from app.repository import training_runs as training_runs_repo

    source = inspect.getsource(training_runs_repo)
    assert "def update" not in source
    assert "UPDATE training_runs" not in source
