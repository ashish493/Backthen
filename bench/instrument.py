"""Span timing + background resource sampler (BENCHMARK_PLAN §4).

Usage:
    rec = Recorder("results/run1.jsonl", run_id="run1", cache_regime="warm", backend="pillow")
    with rec.span("S3_decode", file="a.jpg", bytes_in=123):
        ...
    with ResourceSampler("results/run1_res.jsonl"): ...
"""
import json
import os
import threading
import time

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    _NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    _NV = None


class Recorder:
    """Thread-safe JSONL writer; one row per (run, file, stage)."""

    def __init__(self, path, **common):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self.common = common

    def log(self, **row):
        row = {**self.common, **row}
        with self._lock:
            self._f.write(json.dumps(row) + "\n")

    def span(self, stage, sync=None, **fields):
        return _Span(self, stage, sync, fields)

    def close(self):
        self._f.close()


class _Span:
    def __init__(self, rec, stage, sync, fields):
        self.rec, self.stage, self.sync, self.fields = rec, stage, sync, fields
        self.extra = {}

    def __enter__(self):
        if self.sync:
            self.sync()
        self.t0 = time.perf_counter_ns()
        return self

    def set(self, **kw):
        self.extra.update(kw)

    def __exit__(self, exc_type, *_):
        if self.sync:
            self.sync()
        dt = time.perf_counter_ns() - self.t0
        self.rec.log(stage=self.stage, active_ns=dt, error=bool(exc_type),
                     **{**self.fields, **self.extra})
        return False


class ResourceSampler:
    """Samples CPU/RSS/RAM/GPU/disk every `interval` s on a daemon thread."""

    def __init__(self, path, interval=0.1):
        self.path, self.interval = path, interval
        self._stop = threading.Event()
        self.peak_rss = 0
        self.peak_vram = 0

    def _loop(self):
        proc = psutil.Process()
        last_io = psutil.disk_io_counters()
        last_t = time.perf_counter()
        psutil.cpu_percent(percpu=True)
        with open(self.path, "a", encoding="utf-8") as f:
            while not self._stop.is_set():
                time.sleep(self.interval)
                now = time.perf_counter()
                io = psutil.disk_io_counters()
                rss = proc.memory_info().rss
                row = dict(
                    t=time.time(), cpu_pct=psutil.cpu_percent(),
                    rss=rss, ram_avail=psutil.virtual_memory().available,
                    disk_read_mb_s=(io.read_bytes - last_io.read_bytes) / 1e6 / (now - last_t),
                )
                self.peak_rss = max(self.peak_rss, rss)
                if _NV:
                    u = pynvml.nvmlDeviceGetUtilizationRates(_NV)
                    m = pynvml.nvmlDeviceGetMemoryInfo(_NV)
                    row.update(
                        gpu_util=u.gpu, gpu_mem_util=u.memory, vram=m.used,
                        sm_clock=pynvml.nvmlDeviceGetClockInfo(_NV, pynvml.NVML_CLOCK_SM),
                        temp_c=pynvml.nvmlDeviceGetTemperature(_NV, pynvml.NVML_TEMPERATURE_GPU),
                        power_w=pynvml.nvmlDeviceGetPowerUsage(_NV) / 1000)
                    self.peak_vram = max(self.peak_vram, m.used)
                f.write(json.dumps(row) + "\n")
                last_io, last_t = io, now

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()
