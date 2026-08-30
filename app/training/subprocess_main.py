"""Entrypoint for the training subprocess (ADR 014). Run as:
  python -m app.training.subprocess_main <context_json_path>

Does GPU verification first (GPU_WORKER_MODEL_V0.6.md), then delegates to
the pluggable training body. Emits one JSON line per event to stdout -- the
supervising Worker (app/training/executor.py) reads these and performs every
actual database write itself, fencing-conditioned (ADR 016). This
subprocess never talks to Postgres directly.
"""
import importlib
import json
import sys

from app.training.gpu import verify_gpu


def _report(event: dict) -> None:
    print(json.dumps(event), flush=True)


def main(context_path: str) -> int:
    with open(context_path) as f:
        context = json.load(f)

    gpu_result = verify_gpu(context.get("required_gpu", 0), context.get("required_memory_mb", 0))
    if not gpu_result.ok:
        _report({"event": "gpu_check_failed", "reason": gpu_result.reason})
        return 1
    _report({"event": "gpu_check_passed", "device_count": gpu_result.device_count})

    module_path = context.get("training_entrypoint", "app.training.toy_trainer")
    trainer_module = importlib.import_module(module_path)
    try:
        trainer_module.run(context, _report)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: report and exit non-zero
        _report({"event": "training_error", "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
