"""V0.6's pluggable, dependency-free training body used by this repository's
own tests and CI (no GPU/torch/transformers available in this sandbox --
see V0.6_GPU_VALIDATION.md for the real LoRA/QLoRA SFT run on a real Tesla
T4, validated separately). This is a genuinely real, if minimal, iterative
training loop -- real parameter state, real loss computation, real
checkpoint save/load, real resume continuation -- proving the PLATFORM
mechanics (subprocess, checkpointing, resume, fencing) for real, with the
ML-specific heavy lifting swapped in later behind the same interface
(`run(context, report)` below) without touching any platform code.

Swapping in a real HF/PEFT LoRA trainer means writing a new module with the
same `run(context, report)` signature and pointing TRAINING_ENTRYPOINT at it
-- everything else (Worker supervision, fencing, checkpoint registration,
resume discovery) is unchanged.
"""
import json
import os
import time


def run(context: dict, report) -> None:
    """context: {training_run_id, attempt_number, max_steps,
    checkpoint_every_n_steps, learning_rate, target_value, work_dir,
    resume_from: {local_path, step} | None}
    report(event: dict): callback that emits one JSON-serializable event,
    read by the supervising Worker (app/training/executor.py)."""
    work_dir = context["work_dir"]
    os.makedirs(work_dir, exist_ok=True)

    max_steps = context["max_steps"]
    checkpoint_every_n_steps = context["checkpoint_every_n_steps"]
    learning_rate = context["learning_rate"]
    target_value = context.get("target_value", 1.0)

    # V0.8 hardening: stamp the run/attempt identity into every saved artifact.
    # Checkpoint/final bytes are otherwise a pure function of the hyperparameters
    # (param, step), so two DIFFERENT training runs with identical config produce
    # byte-identical files. Artifact storage is content-addressed (dedup by
    # SHA-256), and `checkpoints.artifact_id` carries a UNIQUE constraint -- so
    # the second such run collided on `checkpoints_artifact_id_key` and failed.
    # This mirrors the existing nonce the evaluation tests already use to keep
    # distinct models from deduping to one artifact. Resume reads only
    # param/step, so these extra keys are inert to the training math.
    identity = {
        "training_run_id": context.get("training_run_id"),
        "attempt_number": context.get("attempt_number"),
    }

    if context.get("resume_from"):
        with open(context["resume_from"]["local_path"]) as f:
            state = json.load(f)
        param = state["param"]
        start_step = state["step"]
        report({"event": "resumed", "step": start_step})
    else:
        param = 0.0
        start_step = 0

    # V0.8: optional per-step wall-clock delay. Defaults to 0 (inert for every
    # existing test/CI path). Used only by scripts/demo_checkpoint_recovery.sh
    # to make the toy trainer run long enough for a deterministic mid-run
    # `docker kill` after a checkpoint is registered but before completion.
    step_sleep_seconds = context.get("step_sleep_seconds", 0)

    step = start_step
    while step < max_steps:
        step += 1
        if step_sleep_seconds:
            time.sleep(step_sleep_seconds)
        error = target_value - param
        param += learning_rate * error
        loss = error * error

        report({"event": "metric", "step": step, "loss": loss, "learning_rate": learning_rate})

        if step % checkpoint_every_n_steps == 0 and step < max_steps:
            local_path = os.path.join(work_dir, f"checkpoint-{step}.json")
            with open(local_path, "w") as f:
                json.dump({"param": param, "step": step, **identity}, f)
            report({"event": "checkpoint", "step": step, "local_path": local_path})

    final_path = os.path.join(work_dir, "final_model.json")
    with open(final_path, "w") as f:
        json.dump({"param": param, "step": step, "final": True, **identity}, f)
    report({"event": "final", "step": step, "local_path": final_path})
