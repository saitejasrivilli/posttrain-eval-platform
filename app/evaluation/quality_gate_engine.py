"""Pure quality-gate evaluation engine (QUALITY_GATE_MODEL_V0.7.md /
ADR 019). Reads ONLY already-persisted aggregate metrics -- it never
recomputes hidden values. Deterministic and type-checked.

Decision semantics:
- PASS: every required rule passed.
- FAIL: at least one required rule failed (and none errored, for `all`).
- ERROR: a required metric is missing, invalid, or an incompatible baseline
  comparison was requested. ERROR is NEVER converted to PASS.
"""
import math

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"

_EQ_TOLERANCE = 1e-9


def _compare(lhs: float, operator: str, rhs: float) -> bool | None:
    """Returns the boolean comparison, or None if the operator is invalid."""
    if operator == ">=":
        return lhs >= rhs
    if operator == ">":
        return lhs > rhs
    if operator == "<=":
        return lhs <= rhs
    if operator == "<":
        return lhs < rhs
    if operator == "==":
        return math.isclose(lhs, rhs, abs_tol=_EQ_TOLERANCE)
    return None


def _evaluate_leaf(rule: dict, metrics: dict, baseline_metrics: dict | None) -> dict:
    """Evaluate a single leaf rule against persisted metrics. Returns a
    structured rule result with status PASS/FAIL/ERROR."""
    metric_name = rule.get("metric")
    operator = rule.get("operator")
    base = {"metric": metric_name, "operator": operator}

    if metric_name is None or operator is None:
        return {**base, "status": ERROR, "reason": "rule missing metric/operator"}
    if metric_name not in metrics:
        return {**base, "status": ERROR, "reason": "metric not present in evaluation results"}

    candidate_value = metrics[metric_name]
    if candidate_value is None or (isinstance(candidate_value, float) and math.isnan(candidate_value)):
        return {**base, "status": ERROR, "reason": "metric value is missing or invalid"}

    # Baseline-delta rule: compare (candidate - baseline) against baseline_delta.
    if "baseline_delta" in rule:
        threshold = rule["baseline_delta"]
        if baseline_metrics is None:
            return {**base, "baseline_delta": threshold, "status": ERROR,
                    "reason": "baseline comparison requested but no compatible baseline metrics"}
        if metric_name not in baseline_metrics or baseline_metrics[metric_name] is None:
            return {**base, "baseline_delta": threshold, "status": ERROR,
                    "reason": "baseline metric not present"}
        delta = candidate_value - baseline_metrics[metric_name]
        outcome = _compare(delta, operator, threshold)
        if outcome is None:
            return {**base, "baseline_delta": threshold, "status": ERROR, "reason": "invalid operator"}
        return {**base, "baseline_delta": threshold, "candidate": candidate_value,
                "baseline": baseline_metrics[metric_name], "delta": delta,
                "status": PASS if outcome else FAIL}

    # Absolute-threshold rule.
    if "value" not in rule:
        return {**base, "status": ERROR, "reason": "rule missing value/baseline_delta"}
    threshold = rule["value"]
    outcome = _compare(candidate_value, operator, threshold)
    if outcome is None:
        return {**base, "value": threshold, "status": ERROR, "reason": "invalid operator"}
    return {**base, "value": threshold, "candidate": candidate_value,
            "status": PASS if outcome else FAIL}


def _combine(child_statuses: list[str], logical: str) -> str:
    if logical == "all":
        if any(s == ERROR for s in child_statuses):
            return ERROR
        if any(s == FAIL for s in child_statuses):
            return FAIL
        return PASS
    # "any"
    if any(s == PASS for s in child_statuses):
        return PASS
    if any(s == ERROR for s in child_statuses):
        return ERROR
    return FAIL


def evaluate(rules: dict, metrics: dict, baseline_metrics: dict | None = None) -> tuple[str, list]:
    """Evaluate a gate's rules. `rules` is a dict with an "all" or "any" key
    holding a list of leaf rules. Returns (status, rule_results)."""
    if not isinstance(rules, dict):
        return ERROR, [{"reason": "rules must be an object with 'all' or 'any'"}]

    logical = None
    if "all" in rules:
        logical = "all"
    elif "any" in rules:
        logical = "any"
    else:
        return ERROR, [{"reason": "rules must contain 'all' or 'any'"}]

    leaves = rules[logical]
    if not isinstance(leaves, list) or not leaves:
        return ERROR, [{"reason": f"'{logical}' must be a non-empty list of rules"}]

    rule_results = [_evaluate_leaf(leaf, metrics, baseline_metrics) for leaf in leaves]
    status = _combine([r["status"] for r in rule_results], logical)
    return status, rule_results
