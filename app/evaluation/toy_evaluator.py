"""V0.7's pluggable, dependency-free evaluator body -- the analogue of
app/training/toy_trainer.py. Runs a genuinely real, if minimal, deterministic
evaluation loop: loads the exact model artifact and dataset artifact bytes
(already downloaded + hash-verified by the supervising Worker), produces a
real per-example prediction, computes real exact_match / token_accuracy /
per-example latency, and emits one JSON event per example plus aggregate
metric events. No GPU/torch needed (this sandbox has none -- see
V0.6_GPU_VALIDATION.md); a real evaluator swaps in behind the same
`run(context, report)` interface without touching any platform code.

Dataset artifact format (JSON):
  {"examples": [{"id": "e1", "input": "...", "expected_output": "..."}, ...]}
Model artifact format (JSON): {"param": <float>, ...}  (final_model.json from
a training run is exactly this shape).
"""
import json
import time

from app.evaluation.metrics import aggregate, exact_match, token_accuracy


def _predict(input_text: str, param: float) -> str:
    """Deterministic toy 'inference': a fixed transform selected by the model
    parameter. A model trained toward target_value>=0.5 (V0.6 toy_trainer)
    lands on the identity transform; an untrained/low-param model reverses the
    tokens. Deterministic: same (input, param) -> same output."""
    tokens = input_text.split()
    if param >= 0.5:
        return " ".join(tokens)
    return " ".join(reversed(tokens))


def run(context: dict, report) -> None:
    with open(context["model_path"]) as f:
        model = json.load(f)
    with open(context["dataset_path"]) as f:
        dataset = json.load(f)

    param = float(model.get("param", 0.0))
    examples = dataset.get("examples", [])
    max_examples = context.get("max_examples")
    if max_examples is not None:
        examples = examples[:max_examples]

    split = context.get("split", "all")
    per_example = []
    for example in examples:
        example_id = str(example["id"])
        input_text = example.get("input", "")
        expected = example.get("expected_output", "")

        start = time.perf_counter()
        prediction = _predict(input_text, param)
        latency_ms = (time.perf_counter() - start) * 1000.0

        em = exact_match(prediction, expected)
        ta = token_accuracy(prediction, expected)
        per_example.append({"exact_match": em, "token_accuracy": ta, "latency_ms": latency_ms})

        report({
            "event": "result",
            "example_id": example_id,
            "prediction": prediction,
            "expected_output": expected,
            "score": em,
            "latency_ms": latency_ms,
        })

    for metric in aggregate(per_example):
        report({
            "event": "metric",
            "metric_name": metric["metric_name"],
            "metric_value": metric["metric_value"],
            "split": split,
            "sample_count": metric["sample_count"],
        })

    report({"event": "final", "example_count": len(per_example)})
