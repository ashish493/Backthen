"""Hypothesis A: do many small files cause significant filesystem overhead?

    python -m bench.exp_a_smallfiles <small_dir> <bundle_dir> [--threads 1 4 16] [--repeats 3]
                                     [--out results/exp_a.jsonl]

<small_dir>  : N small JPEGs in a directory tree (bench.gen_synthetic small)
<bundle_dir> : the SAME total bytes as a few large files (bench.gen_synthetic bundle)
Measures, cold (files evicted) and warm:
  discover : recursive os.scandir listing with stat (s, and microseconds per file)  [small tree only]
  read     : read every byte with N threads (MB/s, files/s)
  hash     : full xxh3 vs partial (size + first/last 64 KiB) over the small files, warm
Decision rule (BENCHMARK_PLAN §6, A): confirmed if per-file overhead exceeds 15% of end-to-end time at the
pipeline's photo rate, or grows non-linearly with file count. Here we report overhead as the extra time per
small-file read vs the same bytes in large files, and compare discovery+hash cost with the ~10 ms/photo compute.
"""
import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from .cache import _disk_read_bytes, evict
from .stages import discover_scandir, hash_full, hash_partial


def files_in(root):
    return sorted(os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs)


def read_file(p):
    n = 0
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            n += len(b)
    return n


def timed_read(paths, threads):
    d0, t0 = _disk_read_bytes(), time.perf_counter()
    with ThreadPoolExecutor(threads) as ex:
        n = sum(ex.map(read_file, paths))
    dt = time.perf_counter() - t0
    return dict(seconds=dt, mb_s=n / 1e6 / dt, files_s=len(paths) / dt, phys=(_disk_read_bytes() - d0) / max(1, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("small")
    ap.add_argument("bundle")
    ap.add_argument("--threads", type=int, nargs="+", default=[1, 4, 16])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="results/exp_a.jsonl")
    a = ap.parse_args()
    small, bundle = files_in(a.small), files_in(a.bundle)
    nbytes_s, nbytes_b = sum(map(os.path.getsize, small)), sum(map(os.path.getsize, bundle))
    out = open(a.out, "w", encoding="utf-8")

    def emit(**r):
        out.write(json.dumps(r) + "\n")
        out.flush()
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})

    emit(kind="info", small_files=len(small), small_mb=nbytes_s / 1e6, bundle_files=len(bundle), bundle_mb=nbytes_b / 1e6)
    for rep in range(a.repeats):
        for regime in ("cold", "warm"):
            for name, paths in (("small", small), ("bundle", bundle)):
                for t in a.threads:
                    if regime == "cold":
                        evict(paths)
                    else:
                        timed_read(paths, 16)
                    emit(kind="read", rep=rep, regime=regime, layout=name, threads=t, n_files=len(paths), **timed_read(paths, t))
        # discovery and hashing (small tree)
        t0 = time.perf_counter()
        found = discover_scandir(a.small)
        dt = time.perf_counter() - t0
        emit(kind="discover", rep=rep, n_files=len(found), seconds=dt, us_per_file=1e6 * dt / len(found))
        timed_read(small, 16)
        t0 = time.perf_counter()
        for p in small:
            hash_full(p)
        t_full = time.perf_counter() - t0
        t0 = time.perf_counter()
        for p in small:
            hash_partial(p, os.path.getsize(p))
        t_part = time.perf_counter() - t0
        emit(kind="hash_warm", rep=rep, n_files=len(small), full_s=t_full, partial_s=t_part,
             full_us_per_file=1e6 * t_full / len(small), partial_us_per_file=1e6 * t_part / len(small))
    out.close()


if __name__ == "__main__":
    main()
