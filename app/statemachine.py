from app.models.job import JobStatus

# See STATE_TRANSITIONS_V0.2.md for the authoritative documentation of this table.
VALID_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.PENDING.value: {JobStatus.QUEUED.value},
    JobStatus.QUEUED.value: {JobStatus.RUNNING.value, JobStatus.CANCELLED.value},
    JobStatus.RUNNING.value: {
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    },
    JobStatus.SUCCEEDED.value: set(),
    JobStatus.FAILED.value: set(),
    JobStatus.CANCELLED.value: set(),
}


def sources_for(to_status: str) -> list[str]:
    """States from which `to_status` is a legal transition target."""
    return [s for s, targets in VALID_TRANSITIONS.items() if to_status in targets]
