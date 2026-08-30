from app.training.gpu import verify_gpu


def test_no_gpu_required_always_passes():
    result = verify_gpu(required_gpu=0, required_memory_mb=0)
    assert result.ok is True


def test_gpu_required_but_unavailable_fails_closed():
    result = verify_gpu(required_gpu=1, required_memory_mb=0, probe=lambda: (False, 0, None))
    assert result.ok is False
    assert result.reason == "cuda_unavailable"


def test_insufficient_gpu_count_fails_closed():
    result = verify_gpu(required_gpu=2, required_memory_mb=0, probe=lambda: (True, 1, 16000))
    assert result.ok is False
    assert result.reason == "insufficient_gpu_count"


def test_insufficient_gpu_memory_fails_closed():
    result = verify_gpu(required_gpu=1, required_memory_mb=20000, probe=lambda: (True, 1, 14560))
    assert result.ok is False
    assert result.reason == "insufficient_gpu_memory"


def test_matching_environment_passes():
    result = verify_gpu(required_gpu=1, required_memory_mb=10000, probe=lambda: (True, 1, 14560))
    assert result.ok is True
    assert result.device_count == 1
