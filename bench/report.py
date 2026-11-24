"""JSONL -> stage time-share table (BENCHMARK_PLAN §10).

Usage: python -m bench.report results/<run>.jsonl [more.jsonl ...]
Excludes *_res.jsonl sampler files automatically. Prints Markdown.
"""
import json
import sys
from collections import defaultdict

import numpy as np

ORDER = ["S0_discover", "S1_hash", "S2_metadata", "S3_decode", "S4_transform",
         "S5_transfer", "S6_inference", "S7_write"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def table(rows, title):
    wall = next((r["active_ns"] for r in rows if r["stage"] == "E2E_wall"), None)
    by = defaultdict(list)
    for r in rows:
        if r["stage"] in ORDER:
            by[(r["stage"], r.get("kind", "-"))].append(r["active_ns"] / 1e6)
    total_active = sum(sum(v) for v in by.values())
    lines = [f"### {title}", "",
             "| Stage | kind | n | total s | % active | p50 ms | p95 ms | p99 ms |",
             "|---|---|--:|--:|--:|--:|--:|--:|"]
    for st in ORDER:
        for (s, k), v in sorted(by.items()):
            if s != st:
                continue
            a = np.array(v)
            lines.append(f"| {s} | {k} | {len(a)} | {a.sum()/1e3:.2f} | {100*a.sum()/total_active:.1f} "
                         f"| {np.percentile(a,50):.1f} | {np.percentile(a,95):.1f} | {np.percentile(a,99):.1f} |")
    if wall:
        n = next(r.get("n_files", 0) for r in rows if r["stage"] == "E2E_wall")
        e = next(r for r in rows if r["stage"] == "E2E_wall")
        lines += ["", f"End-to-end wall: **{wall/1e9:.1f} s** for {n} files "
                      f"({n/(wall/1e9):.2f} files/s); sum of stage time {total_active/1e3:.1f} s; "
                      f"peak RSS {e.get('peak_rss',0)/2**20:.0f} MiB, peak VRAM {e.get('peak_vram',0)/2**20:.0f} MiB"]
    dec = [r for r in rows if r["stage"] == "S3_decode" and r.get("kind") == "video" and r.get("frames_returned")]
    if dec:
        ratio = sum(r["frames_decoded"] for r in dec) / sum(r["frames_returned"] for r in dec)
        init = sum(r["open_ms"] for r in dec) / sum(r["active_ns"] / 1e6 for r in dec)
        lines += [f"Video decode: decoded/returned frames = {ratio:.1f}x; open_ms share of S3 = {100*init:.1f}%"]
    return "\n".join(lines)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        if p.endswith("_res.jsonl"):
            continue
        print(table(load(p), p), "\n")
