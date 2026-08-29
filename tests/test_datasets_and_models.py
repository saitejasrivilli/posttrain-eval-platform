from app.repository import artifacts as artifacts_repo
from app.services import datasets as datasets_service
from app.services import models as models_service


def test_dataset_versions_are_sequential_and_immutable(db_session, storage_client):
    dataset = datasets_service.create_dataset(db_session, "customer_data", None)

    v1 = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"v1 bytes", "u1")
    v2 = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"v2 bytes", "u1")

    assert v1.version_number == 1
    assert v2.version_number == 2
    assert v1.artifact_id != v2.artifact_id


def test_duplicate_content_creates_new_dataset_version_but_shares_artifact(db_session, storage_client):
    """ADR 010: deliberate asymmetry with model registration -- re-registering
    identical content as a dataset version is allowed and creates a NEW
    version row sharing the existing artifact."""
    dataset = datasets_service.create_dataset(db_session, "d1", None)

    v1 = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"same bytes", "u1")
    v2 = datasets_service.create_dataset_version(db_session, storage_client, dataset.id, b"same bytes", "u1")

    assert v1.version_number != v2.version_number
    assert v1.artifact_id == v2.artifact_id  # deduped bytes, shared artifact


def test_duplicate_model_registration_rejected(db_session, storage_client):
    """FAILURE_SCENARIOS_V0.5.md #6: unlike datasets, an artifact maps to at
    most one ModelVersion."""
    from app.services import artifacts as artifacts_service

    artifact = artifacts_service.upload_artifact(db_session, storage_client, b"model weights", "MODEL", "u1")
    model = models_service.create_model(db_session, "my-model", None)

    first = models_service.register_version(db_session, model.id, artifact.id, None)
    assert first.version_number == 1

    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        models_service.register_version(db_session, model.id, artifact.id, None)
    assert exc_info.value.status_code == 409


def test_model_registration_requires_uploaded_artifact(db_session):
    """Hard invariant (ARTIFACT_LIFECYCLE_V0.5.md): a PENDING/nonexistent
    artifact can never be registered."""
    from fastapi import HTTPException
    import pytest

    pending = artifacts_repo.create_pending(db_session, "abc123" * 10, "MODEL")
    model = models_service.create_model(db_session, "m2", None)

    with pytest.raises(HTTPException) as exc_info:
        models_service.register_version(db_session, model.id, pending.id, None)
    assert exc_info.value.status_code == 409


def test_model_registration_is_not_automatic_on_training_success(db_session, storage_client):
    """STATE_TRANSITIONS_V0.5.md #2: the single most important design
    decision -- an UPLOADED artifact from a "successful" training context
    exists independently and is NOT a ModelVersion until explicitly registered."""
    from app.services import artifacts as artifacts_service

    artifact = artifacts_service.upload_artifact(
        db_session, storage_client, b"training output", "MODEL", "u1"
    )

    assert artifact.status == "UPLOADED"
    # No model_versions row exists anywhere referencing this artifact yet.
    assert models_service.__name__  # sanity
    from app.repository import models as models_repo

    assert models_repo.get_version_by_artifact(db_session, artifact.id) is None
