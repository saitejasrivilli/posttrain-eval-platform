import random
from datetime import datetime, timedelta, timezone

# Failure classification, per ADR 005. "unknown" is treated as transient for
# retry purposes -- bounded by MAX_ATTEMPTS same as any transient failure,
# never an infinite retry loop.
TRANSIENT = "transient"
PERMANENT = "permanent"
UNKNOWN = "unknown"

RETRYABLE_CLASSIFICATIONS = {TRANSIENT, UNKNOWN}


def compute_next_retry_at(
    attempt_number: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    jitter_ratio: float,
) -> datetime:
    """Exact policy from ADR 005:
    uncapped = base * 2^(attempt_number - 1)
    capped   = min(uncapped, max_delay)
    jitter   = random_uniform(0, capped * jitter_ratio)
    next_retry_at = now() + capped + jitter
    """
    uncapped = base_delay_seconds * (2 ** (attempt_number - 1))
    capped = min(uncapped, max_delay_seconds)
    jitter = random.uniform(0, capped * jitter_ratio)
    return datetime.now(timezone.utc) + timedelta(seconds=capped + jitter)


def is_retryable(classification: str, attempt_number: int, max_attempts: int) -> bool:
    return classification in RETRYABLE_CLASSIFICATIONS and attempt_number < max_attempts
