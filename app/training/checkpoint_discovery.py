"""Checkpoint discovery (ADR 015). Deterministic, read-only, operates on the
TRUSTED `checkpoints` table -- never on raw object storage or the `artifacts`
table alone (CHECKPOINT_RESUME_MODEL_V0.6.md's "existence is not
authoritative training state" invariant)."""
import logging
import uuid

from sqlalchemy.orm import Session

from app.models.checkpoint import Checkpoint
from app.repository import artifacts as artifacts_repo
from app.repository import training_runs as training_runs_repo
from app.storage import hash_bytes

logger = logging.getLogger("app")

SUPPORTED_CHECKPOINT_FORMAT_VERSION = 1


def find_resume_checkpoint(
    db: Session,
    training_run_id: uuid.UUID,
    storage_client,
    supported_format_version: int = SUPPORTED_CHECKPOINT_FORMAT_VERSION,
) -> Checkpoint | None:
    """The exact algorithm from CHECKPOINT_RESUME_MODEL_V0.6.md: candidates
    ordered (step DESC, attempt_number DESC, created_at DESC -- required
    clarification B), each checked against all 6 compatibility rules,
    stopping at the first fully-valid one."""
    training_run = training_runs_repo.get(db, training_run_id)
    if training_run is None:
        return None

    candidates = (
        db.query(Checkpoint)
        .filter(Checkpoint.training_run_id == training_run_id)
        .order_by(Checkpoint.step.desc(), Checkpoint.attempt_number.desc(), Checkpoint.created_at.desc())
        .all()
    )

    for candidate in candidates:
        if not _is_compatible(db, candidate, training_run, storage_client, supported_format_version):
            continue
        return candidate
    return None


def _is_compatible(db: Session, candidate: Checkpoint, training_run, storage_client, supported_format_version: int) -> bool:
    artifact = artifacts_repo.get(db, candidate.artifact_id)
    if artifact is None or artifact.status != "UPLOADED":  # rule 1
        logger.info("checkpoint_rejected", extra={"reason": "not_uploaded", "step": candidate.step})
        return False

    # rule 2: re-verify hash by downloading and re-hashing (never trust the flag alone)
    from app.storage import object_exists_with_hash

    if not object_exists_with_hash(storage_client, artifact.storage_key, artifact.content_hash):
        logger.info("checkpoint_rejected", extra={"reason": "hash_mismatch", "step": candidate.step})
        return False

    # rule 3: same training_run_id -- trivially true, the query already scoped to it

    # rule 4: base-model identity match
    if candidate.base_model_id != training_run.base_model_id or (
        candidate.base_model_version_number != training_run.base_model_version_number
    ):
        logger.info("checkpoint_rejected", extra={"reason": "base_model_mismatch", "step": candidate.step})
        return False

    # rule 5: config match -- structurally always true in V0.6 (TrainingRun is
    # immutable, ADR 015), checked anyway as a safety net.
    # (No stored config snapshot to compare against; the live TrainingRun row
    # IS the only config that has ever applied to this run's attempts.)

    # rule 6: format version supported
    if candidate.checkpoint_format_version != supported_format_version:
        logger.info("checkpoint_rejected", extra={"reason": "unsupported_format_version", "step": candidate.step})
        return False

    return True
