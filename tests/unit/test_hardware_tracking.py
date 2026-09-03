from __future__ import annotations

from types import SimpleNamespace

from letsaigc.tracking.hardware import GpuMemorySampler


def test_gpu_sampler_records_peak_without_owning_gpu_processes(monkeypatch) -> None:
    values = iter(("1200\n", "1750\n", "1500\n"))

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=next(values))

    monkeypatch.setattr("letsaigc.tracking.hardware.subprocess.run", fake_run)
    sampler = GpuMemorySampler()
    sampler._sample()
    sampler._sample()
    sampler._sample()
    assert sampler.stop() == {"peak_system_used_mib": 1750, "samples": 3}
