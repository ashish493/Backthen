"""Step 4a: storage characterisation. How fast can the disk deliver our media files, cold vs warm?

Usage: python -m bench.exp_storage <dir> [<dir> ...] [--threads 1 2 4 8 16] [--repeats 3]
                                   [--out results/exp_storage.jsonl]
Each thread reads whole files (1 MiB chunks), like the pipeline's workers do. Cold runs evict every
file first (bench.cache.evict, verified by physical-read counters).
"""
import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from .cache import _disk_read_bytes, evict, media_files


def read_file(p, bufsize=1 << 20):
    n = 0
    with open(p, "rb") as f:
        while b := f.read(bufsize):
            n += len(b)
    return n


def run(paths, threads):
    d0, t0 = _disk_read_bytes(), time.perf_counter()
    with ThreadPoolExecutor(threads) as ex:
        nbytes = sum(ex.map(read_file, paths))
    dt = time.perf_counter() - t0
    return dict(seconds=dt, mb_s=nbytes / 1e6 / dt, files_s=len(paths) / dt,
                physical_read_ratio=(_disk_read_bytes() - d0) / nbytes, bytes=nbytes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="results/exp_storage.jsonl")
    ap.add_argument("--exts", nargs="+", help="only these extensions, e.g. .jpg .jpeg")
    ap.add_argument("--stride", type=int, default=1, help="take every k-th file (sorted) as a subset")
    ap.add_argument("--label", help="label written instead of the directory path")
    a = ap.parse_args()
    out = open(a.out, "w", encoding="utf-8")
    for d in a.dirs:
        paths = media_files(d, tuple(a.exts)) if a.exts else media_files(d)
        paths = paths[:: a.stride]
        avg = sum(os.path.getsize(p) for p in paths) / len(paths)
        for regime in ("cold", "warm"):
            for t in a.threads:
                rs = []
                for _ in range(a.repeats):
                    if regime == "cold":
                        evict(paths)
                    else:
                        run(paths, 16)                    # make sure everything is cached
                    rs.append(run(paths, t))
                row = dict(dir=a.label or d, n_files=len(paths), avg_file_mb=avg / 1e6, regime=regime, threads=t,
                           mb_s=statistics.median(r["mb_s"] for r in rs),
                           files_s=statistics.median(r["files_s"] for r in rs),
                           mb_s_min=min(r["mb_s"] for r in rs), mb_s_max=max(r["mb_s"] for r in rs),
                           physical_read_ratio=statistics.median(r["physical_read_ratio"] for r in rs))
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"{(a.label or os.path.basename(d))[:18]:18s} {len(paths):5d} files avg {avg/1e6:6.1f} MB {regime:4s} threads={t:2d} "
                      f"{row['mb_s']:7.0f} MB/s [{row['mb_s_min']:.0f}-{row['mb_s_max']:.0f}] {row['files_s']:7.1f} files/s "
                      f"phys={row['physical_read_ratio']:.2f}")
    out.close()


if __name__ == "__main__":
    main()
