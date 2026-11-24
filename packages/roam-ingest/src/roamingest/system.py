"""OS-specific helpers: cold-cache eviction, power throttling opt-out (ADR-007), resource monitor."""
import os
import statistics
import subprocess
import sys
import threading
import time

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    _NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    pynvml, _NV = None, None

IS_WINDOWS = sys.platform == "win32"


def disk_read_bytes():
    try:
        return sum(d.read_bytes for d in psutil.disk_io_counters(perdisk=True).values())
    except Exception:  # noqa: BLE001
        return 0


def evict(paths):
    """Windows: purge files from the system file cache (open once with FILE_FLAG_NO_BUFFERING; verified by
    physical-read counters in results/EXP_COLD.md). Returns the number of files that failed. No-op elsewhere."""
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    invalid = wintypes.HANDLE(-1).value
    failed = 0
    for p in paths:
        h = k32.CreateFileW(os.path.abspath(p), 0x80000000, 0x7, None, 3, 0x20000000, None)
        if h == invalid or h is None:
            failed += 1
            continue
        k32.CloseHandle(h)
    return failed


def power_plan_name():
    if not IS_WINDOWS:
        return None
    try:
        out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10).stdout
        return out.split("(")[-1].split(")")[0].strip() if "(" in out else out.strip()
    except Exception:  # noqa: BLE001
        return None


PRIORITY = {"normal": "NORMAL_PRIORITY_CLASS", "above": "ABOVE_NORMAL_PRIORITY_CLASS", "high": "HIGH_PRIORITY_CLASS"}


def set_power(throttle="default", priority="normal"):
    """throttle='off' opts this process out of Windows power throttling (EcoQoS); priority sets the class.
    Returns (throttle_off_ok, priority_set)."""
    ok = disable_power_throttling() if throttle == "off" else None
    pr = None
    if priority != "normal":
        try:
            psutil.Process().nice(getattr(psutil, PRIORITY[priority]))
            pr = priority
        except Exception:  # noqa: BLE001
            pr = "failed"
    return ok, pr


def disable_power_throttling():
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class PPTS(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetProcessInformation.restype = wintypes.BOOL
    st = PPTS(1, 0x1, 0x0)                   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED, state 0 = not throttled
    return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)))


class Monitor:
    """Samples CPU, RSS, GPU, NVDEC, disk throughput (and an optional probe) every `interval` seconds."""

    def __init__(self, interval=0.5, probe=None, keep_timeline=True):
        self.interval, self._stop, self.probe, self.keep = interval, threading.Event(), probe, keep_timeline
        self.cpu, self.gpu, self.dec, self.timeline = [], [], [], []
        self.peak_rss = self.peak_vram = self.max_temp = self.max_clock = 0
        self.min_clock = 10**9

    def _loop(self):
        p = psutil.Process()
        psutil.cpu_percent()
        t0 = last_t = time.perf_counter()
        last_d = disk_read_bytes()
        while not self._stop.wait(self.interval):
            now, d = time.perf_counter(), disk_read_bytes()
            row = dict(t=round(now - t0, 1), cpu=psutil.cpu_percent(),
                       disk_mb_s=round((d - last_d) / 1e6 / max(1e-6, now - last_t), 1))
            last_t, last_d = now, d
            self.cpu.append(row["cpu"])
            self.peak_rss = max(self.peak_rss, p.memory_info().rss)
            if _NV:
                try:
                    row["gpu"] = pynvml.nvmlDeviceGetUtilizationRates(_NV).gpu
                    self.gpu.append(row["gpu"])
                    self.peak_vram = max(self.peak_vram, pynvml.nvmlDeviceGetMemoryInfo(_NV).used)
                    row["nvdec"] = pynvml.nvmlDeviceGetDecoderUtilization(_NV)[0]
                    self.dec.append(row["nvdec"])
                    self.max_temp = max(self.max_temp, pynvml.nvmlDeviceGetTemperature(_NV, pynvml.NVML_TEMPERATURE_GPU))
                    clk = pynvml.nvmlDeviceGetClockInfo(_NV, pynvml.NVML_CLOCK_SM)
                    self.min_clock, self.max_clock = min(self.min_clock, clk), max(self.max_clock, clk)
                except Exception:  # noqa: BLE001
                    pass
            if self.probe:
                try:
                    row.update(self.probe())
                except Exception:  # noqa: BLE001
                    pass
            if self.keep:
                self.timeline.append(row)

    def __enter__(self):
        self._d0 = disk_read_bytes()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()
        self.disk_bytes = disk_read_bytes() - self._d0

    def summary(self):
        m = lambda x: round(statistics.mean(x), 1) if x else None  # noqa: E731
        return dict(cpu_mean=m(self.cpu), gpu_util_mean=m(self.gpu), nvdec_util_mean=m(self.dec),
                    peak_rss_mib=round(self.peak_rss / 2**20), peak_vram_mib=round(self.peak_vram / 2**20),
                    gpu_max_temp_c=self.max_temp or None,
                    gpu_sm_clock_mhz_min=None if self.min_clock == 10**9 else self.min_clock,
                    gpu_sm_clock_mhz_max=self.max_clock or None, disk_read_bytes=self.disk_bytes)


# ---------------------------------------------------------------------------------------------- on-disk order
def first_lcn(path):
    """Logical cluster number where a file's data starts (NTFS, FSCTL_GET_RETRIEVAL_POINTERS; no admin needed).
    Used to read files in physical order on hard drives. Returns -1 for files whose data lives inside the MFT
    record (tiny files) or when the volume cannot say (non-NTFS, error): those sort first."""
    if os.name != "nt":
        return -1
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    FILE_READ_ATTRIBUTES, SHARE_ALL, OPEN_EXISTING, BACKUP_SEMANTICS = 0x80, 0x7, 3, 0x02000000
    h = k32.CreateFileW(path, FILE_READ_ATTRIBUTES, SHARE_ALL, None, OPEN_EXISTING, BACKUP_SEMANTICS, None)
    if h in (None, wintypes.HANDLE(-1).value):
        return -1
    try:
        inbuf = ctypes.c_longlong(0)                      # STARTING_VCN_INPUT_BUFFER {StartingVcn = 0}
        out = (ctypes.c_byte * 64)()                      # header (16 bytes) + at least one extent (16 bytes)
        got = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(h, 0x00090073, ctypes.byref(inbuf), 8, out, ctypes.sizeof(out), ctypes.byref(got), None)
        if not ok and ctypes.get_last_error() != 234:     # ERROR_MORE_DATA: first extent still filled in
            return -1
        count = ctypes.c_uint32.from_buffer(out, 0).value
        if count == 0:
            return -1
        return ctypes.c_longlong.from_buffer(out, 24).value   # Extents[0].Lcn (after NextVcn at offset 16)
    finally:
        k32.CloseHandle(h)
