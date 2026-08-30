"""GPU verification (GPU_WORKER_MODEL_V0.6.md). Runs INSIDE the training
subprocess, before any training time is spent. torch is imported lazily --
this module (and the rest of the platform) must not hard-depend on torch
being installed; only a real training subprocess actually needs it."""
from dataclasses import dataclass


@dataclass
class GpuCheckResult:
    ok: bool
    reason: str | None  # None if ok=True
    device_count: int
    total_memory_mb: int | None


def probe_cuda() -> tuple[bool, int, int | None]:
    """Real probe. Isolated into its own function so tests can monkeypatch
    it without needing torch/CUDA installed (GPU_WORKER_MODEL_V0.6.md notes
    this is only checkable where CUDA is actually present)."""
    try:
        import torch
    except ImportError:
        return False, 0, None
    if not torch.cuda.is_available():
        return False, 0, None
    count = torch.cuda.device_count()
    total_mb = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024)) if count else None
    return True, count, total_mb


def verify_gpu(required_gpu: int, required_memory_mb: int, probe=probe_cuda) -> GpuCheckResult:
    """ADR 014/GPU_WORKER_MODEL_V0.6.md: verify the environment actually has
    what the Scheduler's reservation (V0.4) implied. required_gpu=0 always
    passes trivially (a CPU-only job never needed a GPU)."""
    if required_gpu <= 0:
        return GpuCheckResult(ok=True, reason=None, device_count=0, total_memory_mb=None)

    available, device_count, total_mb = probe()
    if not available:
        return GpuCheckResult(ok=False, reason="cuda_unavailable", device_count=0, total_memory_mb=None)
    if device_count < required_gpu:
        return GpuCheckResult(
            ok=False, reason="insufficient_gpu_count", device_count=device_count, total_memory_mb=total_mb
        )
    if total_mb is not None and total_mb < required_memory_mb:
        return GpuCheckResult(
            ok=False, reason="insufficient_gpu_memory", device_count=device_count, total_memory_mb=total_mb
        )
    return GpuCheckResult(ok=True, reason=None, device_count=device_count, total_memory_mb=total_mb)
