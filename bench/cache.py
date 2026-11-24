"""Cold-cache control on Windows without admin rights or extra tools (BENCHMARK_PLAN §5).

evict(paths): opens each file once with FILE_FLAG_NO_BUFFERING and closes it. When no other cached
handle is open, NTFS purges that file's pages from the system cache, so the next normal read must
come from disk. This is only trusted because verify() checks it: physical disk reads (OS counters)
after eviction must equal the bytes read.

Usage: python -m bench.cache verify <dir> [--n 40]
       python -m bench.cache evict <dir>
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

import psutil

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_INVALID = wintypes.HANDLE(-1).value
GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
SHARE_ALL = 0x1 | 0x2 | 0x4
FILE_FLAG_NO_BUFFERING = 0x20000000


def evict(paths):
    """Purge each file from the Windows file cache. Returns number of files that failed to open."""
    failed = 0
    for p in paths:
        h = _k32.CreateFileW(os.path.abspath(p), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING,
                             FILE_FLAG_NO_BUFFERING, None)
        if h == _INVALID or h is None:
            failed += 1
            continue
        _k32.CloseHandle(h)
    return failed


def media_files(root, exts=(".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")):
    return sorted(os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs if f.lower().endswith(exts))


def _disk_read_bytes():
    return sum(d.read_bytes for d in psutil.disk_io_counters(perdisk=True).values())


def read_all(paths, bufsize=1 << 20):
    for p in paths:
        with open(p, "rb") as f:
            while f.read(bufsize):
                pass


def measure(paths):
    nbytes = sum(os.path.getsize(p) for p in paths)
    d0, t0 = _disk_read_bytes(), time.perf_counter()
    read_all(paths)
    dt, dd = time.perf_counter() - t0, _disk_read_bytes() - d0
    return dict(bytes=nbytes, seconds=dt, mb_s=nbytes / 1e6 / dt, physical_read_ratio=dd / nbytes)


def verify(root, n=40):
    paths = media_files(root)[:n]
    read_all(paths)                                   # make sure they are cached
    warm = measure(paths)
    evict(paths)
    cold = measure(paths)
    warm2 = measure(paths)
    for name, m in (("warm", warm), ("after evict", cold), ("warm again", warm2)):
        print(f"{name:12s} {m['bytes']/2**20:7.0f} MiB  {m['mb_s']:8.0f} MB/s  physical reads = {m['physical_read_ratio']:.2f} x bytes")
    ok = cold["physical_read_ratio"] > 0.9 and warm["physical_read_ratio"] < 0.1
    print("eviction works:" , ok)
    return ok, dict(warm=warm, cold=cold, warm_again=warm2)


if __name__ == "__main__":
    cmd, root = sys.argv[1], sys.argv[2]
    if cmd == "verify":
        n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 40
        sys.exit(0 if verify(root, n)[0] else 1)
    paths = media_files(root)
    print(f"evicted {len(paths) - evict(paths)}/{len(paths)} files")
