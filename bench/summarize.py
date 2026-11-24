"""Aggregate results/ingest_*.json into Markdown tables (aggregate numbers only).

    python -m bench.summarize [--prefix A_ B_ AB_ C_]
"""
import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict


def load():
    runs = {}
    for p in sorted(glob.glob("results/ingest_*.json")):
        name = os.path.basename(p)[len("ingest_"):-len(".json")]
        if name.startswith("smoke"):
            continue
        runs[name] = json.load(open(p))
    return runs


def group(runs):
    g = defaultdict(list)
    for n, r in runs.items():
        g[re.sub(r"_r\d+$", "", n)].append(r)
    return g


def med(rs, f):
    v = [f(r) for r in rs if f(r) is not None]
    return statistics.median(v) if v else None


def fmt(x, d=1):
    return "-" if x is None else f"{x:.{d}f}"


def table(g, names):
    lines = ["| run | n | files (photos / videos) | wall s (median) | range | files/s | photos/s | videos/s | MB/s | CPU % | GPU % | NVDEC % | disk MB/s | peak RSS MiB | peak VRAM MiB |",
             "|---|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for n in names:
        rs = g.get(n)
        if not rs:
            continue
        w = [r["wall_s"] for r in rs]
        r0 = rs[0]
        lines.append(f"| {n} | {len(rs)} | {r0['kinds']['photo']['files']} / {r0['kinds']['video']['files']} | {fmt(statistics.median(w))} "
                     f"| {fmt(min(w))}–{fmt(max(w))} | {fmt(med(rs, lambda r: r['files_per_s']), 2)} | {fmt(med(rs, lambda r: r['photos_per_s']), 2)} "
                     f"| {fmt(med(rs, lambda r: r['videos_per_s']), 3)} | {fmt(med(rs, lambda r: r['mb_per_s']))} | {fmt(med(rs, lambda r: r['cpu_mean']), 0)} "
                     f"| {fmt(med(rs, lambda r: r['gpu_util_mean']), 0)} | {fmt(med(rs, lambda r: r['nvdec_util_mean']), 0)} | {fmt(med(rs, lambda r: r['disk_mb_s']), 0)} "
                     f"| {max(r['peak_rss_mib'] for r in rs)} | {max(r['peak_vram_mib'] for r in rs)} |")
    return "\n".join(lines)


def breakdown(r):
    out = []
    for kind in ("photo", "video"):
        k = r["kinds"][kind]
        if not k["files"]:
            continue
        out.append(f"- **{kind}s**: {k['files']} files ({k['bytes']/2**30:.2f} GiB), ok {k['ok']}, errors {k['errors']}, truncated {k['truncated']}, timeouts {k['timeouts']}, "
                   f"embeddings {k['embeddings']}; formats {k['by_ext']}; decoders {k['by_decoder']}; strategies {k['by_strategy']}"
                   + (f"; codecs {k['by_codec']}" if k["by_codec"] else "")
                   + f"; per-file latency ms p50/p95/p99 = {k['latency_ms']['p50']}/{k['latency_ms']['p95']}/{k['latency_ms']['p99']}"
                   + f"; summed stage seconds {k['stage_s']}")
    if r["errors"]:
        out.append(f"- error classes: {r['errors']}")
    if r["other_files_skipped"]:
        out.append(f"- other files skipped by extension: {r['other_files_skipped']}")
    out.append(f"- GPU busy {r['gpu']['busy_s']} s / waiting {r['gpu']['wait_s']} s; reader blocked on byte budget {r['reader_blocked_on_byte_budget_s']} s; "
               f"GPU max temp {r['gpu_max_temp_c']} C, SM clock {r['gpu_sm_clock_mhz_min']}–{r['gpu_sm_clock_mhz_max']} MHz")
    return "\n".join(out)


if __name__ == "__main__":
    runs = load()
    g = group(runs)
    print(table(g, sorted(g)))
    print()
    for n in sorted(g):
        print(f"### {n}\n{breakdown(g[n][0])}\n")
