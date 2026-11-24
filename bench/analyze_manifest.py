"""Aggregate-only breakdown of a manifest (Step 8): by format, codec, resolution class, duration, megapixels.

    python -m bench.analyze_manifest <manifest.db> [--out results/realworld_<tag>.md]

Output contains counts, sizes and timings per CATEGORY only (never a path, name, or per-file row).
"""
import argparse
import collections
import sqlite3

import numpy as np


def res_class(w, h):
    m = max(w or 0, h or 0)
    return "<=720p" if m <= 1280 else "1080p" if m <= 1920 else "1440p" if m <= 2560 else "4K" if m <= 3840 else ">4K"


def mp_class(w, h):
    mp = (w or 0) * (h or 0) / 1e6
    return "<2 MP" if mp < 2 else "2-8 MP" if mp < 8 else "8-12 MP" if mp < 12 else "12-20 MP" if mp < 20 else ">=20 MP"


def dur_class(d):
    return "unknown" if d is None else "<10 s" if d < 10 else "10-60 s" if d < 60 else "1-10 min" if d < 600 else ">10 min"


def stats(rows, total_cost):
    ms = [(r["t_read_ms"] or 0) + (r["t_decode_ms"] or 0) + (r["t_xform_ms"] or 0) for r in rows]
    gib = sum(r["size"] for r in rows) / 2**30
    return (f"{len(rows)} | {gib:.2f} | {np.percentile(ms, 50):.0f} | {np.percentile(ms, 95):.0f} | {sum(ms) / 1000:.1f} "
            f"| {100 * sum(ms) / total_cost:.1f}%")


def table(title, rows, keyf, total_cost, order=None):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    keys = order or sorted(g, key=lambda k: -len(g[k]))
    out = [f"### {title}", "", "| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |", "|---|--:|--:|--:|--:|--:|--:|"]
    for k in keys:
        if k in g:
            out.append(f"| {k} | {stats(g[k], total_cost)} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM files")]
    ok = [r for r in rows if r["status"] != "error"]
    photos = [r for r in ok if r["kind"] == "photo"]
    videos = [r for r in ok if r["kind"] == "video"]
    cost = lambda rs: sum((r["t_read_ms"] or 0) + (r["t_decode_ms"] or 0) + (r["t_xform_ms"] or 0) for r in rs)  # noqa: E731
    total = cost(ok)
    out = [f"## Manifest breakdown (aggregate only)", "",
           f"{len(rows)} files: {len(photos)} photos, {len(videos)} videos, {len(rows) - len(ok)} errors "
           f"({dict(collections.Counter((r['kind'], r['error']) for r in rows if r['status'] == 'error'))}). "
           f"Summed per-file time (read+decode+transform) {total / 1000:.0f} s: photos {100 * cost(photos) / total:.1f}%, videos {100 * cost(videos) / total:.1f}%.", ""]
    out += [table("Photos by format and decode path", photos, lambda r: f"{r['ext']} / {r['strategy']}", total), "",
            table("Photos by resolution", photos, lambda r: mp_class(r["width"], r["height"]), total,
                  ["<2 MP", "2-8 MP", "8-12 MP", "12-20 MP", ">=20 MP"]), "",
            table("Videos by codec and decoder", videos, lambda r: f"{r['codec']} / {r['decoder']}", total), "",
            table("Videos by container", videos, lambda r: r["ext"], total), "",
            table("Videos by resolution", videos, lambda r: res_class(r["width"], r["height"]), total,
                  ["<=720p", "1080p", "1440p", "4K", ">4K"]), "",
            table("Videos by duration", videos, lambda r: dur_class(r["dur_s"]), total,
                  ["<10 s", "10-60 s", "1-10 min", ">10 min", "unknown"]), "",
            table("Videos by sampling strategy", videos, lambda r: r["strategy"], total), ""]
    fr = [(r["frames_decoded"] or 0, r["n_emb"] or 0, r["strategy"]) for r in videos if r["n_emb"]]
    for s in ("seq", "seek"):
        x = [(d, n) for d, n, st in fr if st == s]
        if x:
            out.append(f"- frames decoded per frame returned, strategy {s}: {sum(d for d, _ in x) / sum(n for _, n in x):.1f}x over {len(x)} videos")
    text = "\n".join(out)
    print(text)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text + "\n")


if __name__ == "__main__":
    main()
