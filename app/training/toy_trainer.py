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

    if context.get("resume_from"):
        with open(context["resume_from"]["local_path"]) as f:
            state = json.load(f)
        param = state["param"]
        start_step = state["step"]
        report({"event": "resumed", "step": start_step})
    else:
        param = 0.0
        start_step = 0

    step = start_step
    while step < max_steps:
        step += 1
        error = target_value - param
        param += learning_rate * error
        loss = error * error

        report({"event": "metric", "step": step, "loss": loss, "learning_rate": learning_rate})

        if step % checkpoint_every_n_steps == 0 and step < max_steps:
            local_path = os.path.join(work_dir, f"checkpoint-{step}.json")
            with open(local_path, "w") as f:
                json.dump({"param": param, "step": step}, f)
            report({"event": "checkpoint", "step": step, "local_path": local_path})

    final_path = os.path.join(work_dir, "final_model.json")
    with open(final_path, "w") as f:
        json.dump({"param": param, "step": step, "final": True}, f)
    report({"event": "final", "step": step, "local_path": final_path})
