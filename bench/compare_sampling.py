"""Compare video-frame embeddings of two manifests: an exact reference vs a test sampling policy.

    python -m bench.compare_sampling <reference.db> <test.db> [--max-frames 32] [--label X] [--out results/x.json]

Aggregate output only. All quality numbers are MACRO-averaged over videos (each video counts once), so a few
long videos cannot hide poor coverage on short clips. Per video:
  forward coverage   : for each reference (exact) frame, best cosine to any test frame       -> median, p5
  backward coverage  : for each test frame, best cosine to any reference frame             -> median, p5
                       (low values = the policy introduced content that matches no exact sample)
  displacement       : |target time - chosen frame time| in seconds, per test frame        -> median, max
  retention          : distinct test frames / requested targets
  redundancy (context): for each reference frame, best cosine to another reference frame of the same video
  identity (exact-vs-exact checks): share of same-index frames with identical embedding / identical timestamp

GATE (fixed 21 Nov 2026 before any run, see the approved plan):
  pass if  (a) >= 95% of videos have per-video median forward coverage >= 0.95
      and  (b) macro p5 >= 0.90, where macro p5 = mean over videos of each video's 5th-percentile forward coverage.
  Temporal displacement and retention are reported, not gated.
"""
import argparse
import json
import sqlite3

import numpy as np

from .ingest import sample_targets


def load(db):
    con = sqlite3.connect(db)
    meta = {p: (d, k) for p, d, k in con.execute("SELECT path, dur_s, kind FROM files")}
    emb = {}
    for path, idx, pts, vec in con.execute("SELECT path, idx, pts, vec FROM emb ORDER BY path, idx"):
        if meta.get(path, (None, None))[1] != "video":
            continue
        emb.setdefault(path, []).append((idx, pts, np.frombuffer(vec, dtype=np.float16).astype(np.float32)))
    con.close()
    return emb, meta


def compare(er, et, meta, max_frames=32):
    per = []
    for p in sorted(set(er) & set(et)):
        R = np.stack([v for _, _, v in er[p]])
        T = np.stack([v for _, _, v in et[p]])
        sim = R @ T.T
        fwd, bwd = sim.max(1), sim.max(0)
        red = None
        if len(R) > 1:
            rr = R @ R.T
            np.fill_diagonal(rr, -1)
            red = float(np.median(rr.max(1)))
        d = meta[p][0]
        tg = sample_targets(d, max_frames) if d else None
        disp = [abs(tg[i] - pts) for i, pts, _ in et[p] if tg and i < len(tg)]
        ri = {i: (pts, v) for i, pts, v in er[p]}
        same = [(np.array_equal(ri[i][1], v), abs(ri[i][0] - pts) < 1e-6) for i, pts, v in et[p] if i in ri]
        per.append(dict(fwd_med=float(np.median(fwd)), fwd_p5=float(np.percentile(fwd, 5)),
                        bwd_med=float(np.median(bwd)), bwd_p5=float(np.percentile(bwd, 5)),
                        disp_med=float(np.median(disp)) if disp else None, disp_max=float(max(disp)) if disp else None,
                        retention=len({round(pts, 6) for _, pts, _ in et[p]}) / (len(tg) if tg else len(R)),
                        redundancy=red, ident=[s[0] for s in same], pts=[s[1] for s in same]))
    return per


def summarise(per, label=""):
    if not per:
        return dict(label=label, videos=0)
    col = lambda k: [x[k] for x in per if x[k] is not None]  # noqa: E731
    med = lambda v: round(float(np.median(v)), 4) if v else None  # noqa: E731
    p5 = lambda v: round(float(np.percentile(v, 5)), 4) if v else None  # noqa: E731
    frac_ok = float(np.mean([x["fwd_med"] >= 0.95 for x in per]))
    macro_p5 = float(np.mean(col("fwd_p5")))
    ident = [b for x in per for b in x["ident"]]
    pts = [b for x in per for b in x["pts"]]
    rep = dict(
        label=label, videos=len(per),
        fwd_coverage_macro_median=med(col("fwd_med")), fwd_coverage_macro_p5=round(macro_p5, 4),
        fwd_coverage_worst_video_median=round(min(col("fwd_med")), 4),
        videos_with_median_fwd_below_095=sum(x["fwd_med"] < 0.95 for x in per),
        share_videos_median_fwd_ge_095=round(frac_ok, 4),
        bwd_coverage_macro_median=med(col("bwd_med")), bwd_coverage_macro_p5=round(float(np.mean(col("bwd_p5"))), 4),
        displacement_s_macro_median=med(col("disp_med")), displacement_s_p95_of_video_max=round(float(np.percentile(col("disp_max"), 95)), 3) if col("disp_max") else None,
        displacement_s_max=round(max(col("disp_max")), 3) if col("disp_max") else None,
        retention_macro_mean=round(float(np.mean(col("retention"))), 4), retention_worst=round(min(col("retention")), 4),
        redundancy_macro_median=med(col("redundancy")), redundancy_macro_p5=p5(col("redundancy")),
        same_index_identical_embeddings=round(float(np.mean(ident)), 4) if ident else None,
        same_index_same_pts=round(float(np.mean(pts)), 4) if pts else None)
    rep["gate_a_share_ge_095"] = frac_ok >= 0.95
    rep["gate_b_macro_p5_ge_090"] = macro_p5 >= 0.90
    rep["gate_pass"] = rep["gate_a_share_ge_095"] and rep["gate_b_macro_p5_ge_090"]
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("test")
    ap.add_argument("--max-frames", type=int, default=32)
    ap.add_argument("--label", default="")
    ap.add_argument("--out")
    a = ap.parse_args()
    er, meta = load(a.ref)
    et, _ = load(a.test)
    rep = summarise(compare(er, et, meta, a.max_frames), a.label)
    print(json.dumps(rep, indent=1))
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
