"""Deterministic, dependency-free evaluation metric functions
(REQUIREMENTS_V0.7.md "Initial metrics"). These are PURE functions: the same
inputs always produce the same output, which is what the determinism
acceptance criterion is tested against directly (no GPU/torch needed, exactly
like app/training/toy_trainer.py proves the platform mechanics).

The metric set is deliberately small and only claimed meaningful for the
declared text task_type -- it is NOT claimed universal for arbitrary LLM
tasks (REQUIREMENTS_V0.7.md).
"""


def exact_match(prediction: str, expected: str) -> float:
    """1.0 iff the (stripped) strings are identical, else 0.0."""
    return 1.0 if (prediction or "").strip() == (expected or "").strip() else 0.0


def token_accuracy(prediction: str, expected: str) -> float:
    """Fraction of expected whitespace tokens matched positionally. Length
    mismatch is penalized: the denominator is max(len(pred), len(expected))."""
    pred_tokens = (prediction or "").split()
    exp_tokens = (expected or "").split()
    denom = max(len(pred_tokens), len(exp_tokens))
    if denom == 0:
        return 1.0
    matches = sum(1 for p, e in zip(pred_tokens, exp_tokens) if p == e)
    return matches / denom


def percentile(values: list[float], p: float) -> float:
    """Deterministic nearest-rank-ish percentile via linear interpolation on
    the sorted values. p in [0, 100]. Empty -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


def aggregate(per_example: list[dict]) -> list[dict]:
    """Given per-example records ({"exact_match", "token_accuracy",
    "latency_ms"}), produce the aggregate metric rows. Deterministic for the
    accuracy metrics; latency percentiles depend on measured wall-clock and
    are reported with documented tolerance (FAILURE_SCENARIOS_V0.7.md #24).
    """
    n = len(per_example)
    if n == 0:
        return []
    em = sum(r["exact_match"] for r in per_example) / n
    ta = sum(r["token_accuracy"] for r in per_example) / n
    latencies = [r["latency_ms"] for r in per_example if r.get("latency_ms") is not None]
    metrics = [
        {"metric_name": "exact_match", "metric_value": em, "sample_count": n},
        {"metric_name": "token_accuracy", "metric_value": ta, "sample_count": n},
    ]
    if latencies:
        metrics.extend([
            {"metric_name": "latency_mean_ms",
             "metric_value": sum(latencies) / len(latencies), "sample_count": len(latencies)},
            {"metric_name": "latency_p50_ms",
             "metric_value": percentile(latencies, 50), "sample_count": len(latencies)},
            {"metric_name": "latency_p95_ms",
             "metric_value": percentile(latencies, 95), "sample_count": len(latencies)},
        ])
    return metrics
