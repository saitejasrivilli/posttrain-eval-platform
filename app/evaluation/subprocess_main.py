"""Entrypoint for the evaluation subprocess (mirrors
app/training/subprocess_main.py / ADR 014). Run as:
  python -m app.evaluation.subprocess_main <context_json_path>

Order of operations:
1. GPU verification (reused app/training/gpu.py) -- fail closed if the
   environment lacks the GPU the reservation implied.
2. Model + dataset artifact identity verification: re-hash the local bytes the
   Worker downloaded and compare against the expected content hashes from the
   registered artifacts (V0.5 hash model). A mismatch is fail-closed -- corrupt
   model/dataset artifacts never produce metrics (FAILURE_SCENARIOS_V0.7.md
   #11, #12).
3. Delegate to the pluggable evaluator body, emitting one JSON line per event.

Like the training subprocess, this process NEVER touches Postgres. The
supervising Worker (app/evaluation/executor.py) reads these events and performs
every fencing-conditioned database write itself (ADR 016).
"""
import hashlib
import importlib
import json
import sys

from app.training.gpu import verify_gpu


def _report(event: dict) -> None:
    print(json.dumps(event), flush=True)


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(context_path: str) -> int:
    with open(context_path) as f:
        context = json.load(f)

    gpu_result = verify_gpu(context.get("required_gpu", 0), context.get("required_memory_mb", 0))
    if not gpu_result.ok:
        _report({"event": "gpu_check_failed", "reason": gpu_result.reason})
        return 1
    _report({"event": "gpu_check_passed", "device_count": gpu_result.device_count})

    for kind in ("model", "dataset"):
        expected = context.get(f"{kind}_expected_hash")
        actual = _hash_file(context[f"{kind}_path"])
        if expected is not None and actual != expected:
            _report({"event": "artifact_check_failed", "artifact": kind,
                     "expected_hash": expected, "actual_hash": actual})
            return 1
    _report({"event": "artifact_check_passed"})

    module_path = context.get("evaluator_entrypoint", "app.evaluation.toy_evaluator")
    evaluator_module = importlib.import_module(module_path)
    try:
        evaluator_module.run(context, _report)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: report and exit non-zero
        _report({"event": "evaluation_error", "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
