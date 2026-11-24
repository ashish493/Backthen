"""Correctness tests for bench.ingest (aggregate output only).

    python -m bench.verify_ingest <root> [--work out/verify] [--only T1,T5,...]

T1 determinism        : different worker counts / readers / schedule -> bit-identical embeddings
T2 kill + resume      : hard-kill after N committed files, resume -> identical to uninterrupted run, nothing redone
T3 unchanged rerun    : second run on the same tree processes 0 files
T4 seek == sequential : forced seek vs forced sequential CPU decode -> identical frames and embeddings
T5 smart == sequential: smart strategy vs sequential (CPU) -> identical frames and embeddings
T6 NVDEC seek         : NVDEC seek picks the same frames (timestamps) as CPU seek; embeddings within tolerance
T7 resume per policy  : kill + resume under smart, keyframe and adaptive -> identical to an uninterrupted run
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import numpy as np


def run(args):
    p = subprocess.run([sys.executable, "-m", "bench.ingest", *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def load(db):
    con = sqlite3.connect(db)
    files = {r[0]: r[1:] for r in con.execute("SELECT path,kind,status,n_emb,decoder,cid FROM files")}
    emb = {}
    for path, idx, pts, vec in con.execute("SELECT path,idx,pts,vec FROM emb ORDER BY path,idx"):
        emb.setdefault(path, []).append((idx, pts, bytes(vec)))
    con.close()
    return files, emb


def compare(a, b, kind=None):
    fa, ea = a
    fb, eb = b
    paths = sorted(p for p in fa if (kind is None or fa[p][0] == kind))
    missing = [p for p in paths if p not in fb]
    diff_files = [p for p in paths if p in fb and ea.get(p) != eb.get(p)]
    diff_status = [p for p in paths if p in fb and fa[p][1] != fb[p][1]]
    return dict(files=len(paths), missing=len(missing), embedding_diffs=len(diff_files), status_diffs=len(diff_status))


def pts_and_cos(a, b, kind="video"):
    """Same frames (timestamps) and minimum cosine between same-index embeddings."""
    fa, ea = a
    _, eb = b
    same, cos = [], []
    for p in (p for p in fa if fa[p][0] == kind):
        ra, rb = {i: (t, v) for i, t, v in ea.get(p, [])}, {i: (t, v) for i, t, v in eb.get(p, [])}
        for i in ra:
            if i in rb:
                same.append(abs(ra[i][0] - rb[i][0]) < 1e-6)
                x = np.frombuffer(ra[i][1], np.float16).astype(np.float32)
                y = np.frombuffer(rb[i][1], np.float16).astype(np.float32)
                cos.append(float(x @ y))
    return dict(frames=len(same), same_pts_share=round(float(np.mean(same)), 4) if same else None,
                min_cosine=round(min(cos), 4) if cos else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--work", default="out/verify")
    ap.add_argument("--kill-after", type=int, default=60)
    ap.add_argument("--only")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    want = lambda t: only is None or t in only  # noqa: E731
    shutil.rmtree(a.work, ignore_errors=True)
    W = lambda n: os.path.join(a.work, n)  # noqa: E731
    db = lambda n: os.path.join(W(n), "manifest.sqlite")  # noqa: E731
    res = {}

    if want("T1") or want("T2"):
        run([a.root, "--out-dir", W("base")])
        base = load(db("base"))
    if want("T1"):
        for name, extra in (("workers5_readers1_v3_1", ["--photo-workers", "5", "--readers", "1", "--video-cpu", "3", "--video-nvdec", "1"]),
                            ("phased_hdd", ["--schedule", "phased", "--storage", "hdd", "--photo-workers", "8"])):
            rc, _, _ = run([a.root, "--out-dir", W(name), *extra])
            res[f"T1_{name}"] = dict(rc=rc, **compare(base, load(db(name))))
    if want("T2") or want("T3"):
        rc1, _, _ = run([a.root, "--out-dir", W("kr"), "--kill-after-files", str(a.kill_after)])
        part = load(db("kr"))
        run([a.root, "--out-dir", W("kr"), "--report", os.path.join(W("kr"), "r.json")])
        rep = json.load(open(os.path.join(W("kr"), "r.json")))
        full = load(db("kr"))
        if want("T2"):
            res["T2_kill_resume"] = dict(first_run_rc=rc1, committed_before_kill=len(part[0]),
                                         resumed_skipped=rep["files_resumed_skipped"], processed_after_resume=rep["files_processed"],
                                         total_files=len(full[0]), **compare(base, full) if want("T1") else {})
        if want("T3"):
            run([a.root, "--out-dir", W("kr"), "--report", os.path.join(W("kr"), "r2.json")])
            rep2 = json.load(open(os.path.join(W("kr"), "r2.json")))
            res["T3_unchanged_rerun"] = dict(files_processed=rep2["files_processed"], files_resumed_skipped=rep2["files_resumed_skipped"])
    if want("T4") or want("T5"):
        for strat in ("seq", "seek", "smart"):
            run([a.root, "--out-dir", W("cpu_" + strat), "--video-strategy", strat, "--video-nvdec", "0"])
        if want("T4"):
            res["T4_seek_vs_seq"] = compare(load(db("cpu_seq")), load(db("cpu_seek")), "video")
        if want("T5"):
            res["T5_smart_vs_seq"] = compare(load(db("cpu_seq")), load(db("cpu_smart")), "video")
    if want("T6"):
        run([a.root, "--out-dir", W("cpu_seek6"), "--video-strategy", "seek", "--video-nvdec", "0"])
        run([a.root, "--out-dir", W("nv_seek6"), "--video-strategy", "seek", "--nvdec-seek", "--nvdec-pct", "100",
             "--video-cpu", "0", "--video-nvdec", "2"])
        res["T6_nvdec_seek_vs_cpu_seek"] = pts_and_cos(load(db("cpu_seek6")), load(db("nv_seek6")))
    if want("T7"):
        for pol, extra in (("smart", ["--video-strategy", "smart"]), ("keyframe", ["--video-sampling", "keyframe"]),
                           ("adaptive", ["--video-sampling", "adaptive", "--adaptive-tau", "1.0"])):
            extra = extra + ["--nvdec-seek"]
            run([a.root, "--out-dir", W("t7_full_" + pol), *extra])
            rc1, _, _ = run([a.root, "--out-dir", W("t7_kr_" + pol), *extra, "--kill-after-files", str(a.kill_after)])
            run([a.root, "--out-dir", W("t7_kr_" + pol), *extra, "--report", os.path.join(W("t7_kr_" + pol), "r.json")])
            rep = json.load(open(os.path.join(W("t7_kr_" + pol), "r.json")))
            res[f"T7_resume_{pol}"] = dict(first_run_rc=rc1, resumed_skipped=rep["files_resumed_skipped"],
                                           **compare(load(db("t7_full_" + pol)), load(db("t7_kr_" + pol))))

    print(json.dumps(res, indent=1))
    checks = []
    for k, v in res.items():
        if "embedding_diffs" in v:
            checks.append(v["embedding_diffs"] == 0 and v["missing"] == 0 and v["status_diffs"] == 0)
        if k.startswith("T2") or k.startswith("T7"):
            checks.append(v["resumed_skipped"] > 0)
        if k.startswith("T3"):
            checks.append(v["files_processed"] == 0)
        if k.startswith("T6"):
            checks.append(v["same_pts_share"] == 1.0 and v["min_cosine"] >= 0.98)
    print("ALL PASS" if all(checks) else "SOME TESTS FAILED", f"({sum(checks)}/{len(checks)} checks)")


if __name__ == "__main__":
    main()
