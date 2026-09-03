from __future__ import annotations

import subprocess
import threading


class GpuMemorySampler:
    """Sample system-wide NVIDIA memory usage without controlling GPU processes."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        self.interval_seconds = interval_seconds
        self.peak_used_mib: int | None = None
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            value = int(completed.stdout.splitlines()[0].strip())
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return
        self.samples += 1
        self.peak_used_mib = value if self.peak_used_mib is None else max(self.peak_used_mib, value)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="letsaigc-gpu-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, int | None]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        return {"peak_system_used_mib": self.peak_used_mib, "samples": self.samples}
